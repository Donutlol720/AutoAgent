"""Deterministic browser operations for the Browser Use 1.0 / Browser Harness REPL.

IMPORTANT: Browser Harness helpers are injected into the `browser-use` Python
execution environment. They are not importable directly from `browser_harness`.
Pass the injected functions into BrowserTools, or use BrowserTools.from_repl(globals()).

Read operations never navigate, refresh, create, or close tabs. `open_tab` is
explicitly separate from reading and reuses a matching tab when possible.
This is a reliability interface, not a security boundary.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

MAX_TEXT_CHARS = 12000
MAX_JSON_CHARS = 200000


class BrowserToolError(RuntimeError):
    """A browser operation failed or would be ambiguous."""


def normalize_url(value: str) -> tuple[str, str, str, str]:
    """Compare HTTP(S) URLs by scheme, host/port, path and query.

    Ignore fragment identifiers, a trailing path slash, and casing of the host.
    Paths and query strings remain case-sensitive. No redirects are followed.
    """
    if not isinstance(value, str) or not value.strip():
        raise BrowserToolError("URL must be a nonempty string")
    value = value.strip()
    if len(value) > 4096:
        raise BrowserToolError("URL is too long")
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            raise BrowserToolError("Only http:// and https:// URLs are supported")
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise BrowserToolError("URL requires a hostname and cannot contain credentials")
        host = parsed.hostname.lower()
        port = parsed.port  # raises ValueError for malformed ports
    except ValueError as exc:
        raise BrowserToolError(f"Malformed URL: {exc}") from exc
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # Normalize IPv6 hosts
    default_port = 80 if scheme == "http" else 443
    authority = host if port in (None, default_port) else f"{host}:{port}"
    return scheme, authority, (parsed.path or "/").rstrip("/") or "/", parsed.query


# Fixed, non-model-generated JavaScript expression. Never interpolate URLs or
# other untrusted values into JavaScript.
SNAPSHOT_JS = "({url:location.href,title:document.title,text:document.body?.innerText ?? ''})"


class BrowserTools:
    """Browser tools whose concrete helpers are injected by the Browser Use REPL."""

    def __init__(self, *, list_tabs, switch_tab, js, new_tab):
        for label, fn in (
            ("list_tabs", list_tabs), ("switch_tab", switch_tab),
            ("js", js), ("new_tab", new_tab),
        ):
            if not callable(fn):
                raise BrowserToolError(f"Missing browser helper: {label}")
        self._list_tabs = list_tabs
        self._switch_tab = switch_tab
        self._js = js
        self._new_tab = new_tab

    @classmethod
    def from_repl(cls, namespace: dict) -> "BrowserTools":
        """Build from Browser Use's injected REPL globals (no harness imports)."""
        return cls(**{name: namespace.get(name) for name in
                      ("list_tabs", "switch_tab", "js", "new_tab")})

    def list_tabs(self) -> list[dict]:
        """Return real Chrome pages, without changing the attached tab."""
        tabs = self._list_tabs(include_chrome=False)
        if not isinstance(tabs, list):
            raise BrowserToolError("Browser Harness did not return a tab list")
        result = []
        for tab in tabs:
            if not isinstance(tab, dict):
                continue
            target_id = tab.get("targetId") or tab.get("target_id")
            if not target_id:
                continue
            result.append({
                "target_id": target_id,
                "url": tab.get("url") or "",
                "title": (tab.get("title") or "").removeprefix("🐴 "),
            })
        return result

    def _matching_tabs(self, url: str) -> list[dict]:
        expected = normalize_url(url)
        found = []
        for tab in self.list_tabs():
            try:
                if normalize_url(tab["url"]) == expected:
                    found.append(tab)
            except BrowserToolError:
                continue  # Ignore non-HTTP(S) internal/browser pages.
        return found

    def _select_existing(self, url: str) -> dict:
        matches = self._matching_tabs(url)
        if not matches:
            raise BrowserToolError(f"No existing Chrome tab matches {url!r}")
        if len(matches) != 1:
            raise BrowserToolError(
                f"Expected exactly one tab for {url!r}, found {len(matches)}; "
                "close duplicate tabs first"
            )
        self._switch_tab(matches[0]["target_id"])
        return matches[0]

    def _snapshot(self, expected_url: str) -> dict:
        observed = self._js(SNAPSHOT_JS)
        if not isinstance(observed, dict):
            raise BrowserToolError(f"JavaScript returned {type(observed).__name__}, not an object")
        actual_url = observed.get("url")
        if not isinstance(actual_url, str):
            raise BrowserToolError("Could not read the attached page URL")
        if normalize_url(actual_url) != normalize_url(expected_url):
            raise BrowserToolError(
                f"Attached to the wrong page: expected {expected_url!r}, got {actual_url!r}"
            )
        page_text = observed.get("text")
        if not isinstance(page_text, str):
            raise BrowserToolError("Could not read document.body.innerText")
        return {
            "url": actual_url,
            "title": str(observed.get("title") or "").removeprefix("🐴 "),
            "text": page_text,
        }

    def read_tab(self, url: str) -> dict:
        """Read page text/title from exactly one ALREADY-OPEN tab; never navigate."""
        tab = self._select_existing(url)
        observed = self._snapshot(url)
        text = observed.pop("text")
        return {
            **observed,
            "target_id": tab["target_id"],
            "text": text[:MAX_TEXT_CHARS],
            "text_truncated": len(text) > MAX_TEXT_CHARS,
        }

    def read_json(self, url: str) -> dict:
        """Read and decode the complete JSON body of an existing tab."""
        tab = self._select_existing(url)
        observed = self._snapshot(url)
        raw = observed["text"].strip()
        if len(raw) > MAX_JSON_CHARS:
            raise BrowserToolError("JSON body exceeds the configured size limit")
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise BrowserToolError(f"The tab does not contain valid JSON: {exc}") from exc
        return {"url": observed["url"], "target_id": tab["target_id"], "data": parsed}

    def open_tab(self, url: str) -> dict:
        """Reuse one exact matching tab or explicitly open one; verify final URL."""
        normalize_url(url)  # Validate before any browser action.
        matches = self._matching_tabs(url)
        if len(matches) > 1:
            raise BrowserToolError(f"Cannot open: {len(matches)} matching tabs already exist")
        if len(matches) == 1:
            target_id = matches[0]["target_id"]
            self._switch_tab(target_id)
            created = False
        else:
            target_id = self._new_tab(url)
            created = True
        observed = self._snapshot(url)
        return {
            "url": observed["url"],
            "title": observed["title"],
            "created": created,
            "reused": not created,
            "target_id": target_id if isinstance(target_id, str) else str(target_id),
        }

    def execute(self, request: dict) -> dict | list[dict]:
        """Strict, structured dispatch. No freeform Python or JS arguments."""
        if not isinstance(request, dict):
            raise BrowserToolError("Action request must be a JSON object")
        action = request.get("action")
        if action == "list_tabs":
            if set(request) != {"action"}:
                raise BrowserToolError("list_tabs accepts no arguments")
            return self.list_tabs()
        if action not in ("read_tab", "read_json", "open_tab"):
            raise BrowserToolError(f"Unsupported browser action: {action!r}")
        if set(request) != {"action", "url"}:
            raise BrowserToolError(f"{action} requires only a 'url' argument")
        return getattr(self, action)(request["url"])
