"""Run a single structured browser action in the existing Chrome session.

Examples (from the AutoAgent directory):
    .venv/bin/python browser_action.py list_tabs
    .venv/bin/python browser_action.py read_json https://httpbin.org/uuid
    .venv/bin/python browser_action.py read_tab https://example.com
    .venv/bin/python browser_action.py open_tab https://example.com

No Ollama requests are made. The only browser-creating operation is open_tab.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULT_MARKER = "AUTOAGENT_BROWSER_ACTION_RESULT="


def call_browser_action(request: dict, *, timeout: int = 45) -> dict:
    """Send a validated JSON request to the Browser Use CLI's REPL."""
    browser_use = Path(sys.executable).with_name("browser-use")
    if not browser_use.is_file():
        return {"ok": False, "error": f"browser-use CLI not found: {browser_use}"}

    # Keep request and path as quoted Python string literals, never shell code.
    script = f'''
import json
import sys
sys.path.insert(0, {str(ROOT)!r})
from browser_tools import BrowserTools
request = json.loads({json.dumps(json.dumps(request))})
try:
    tools = BrowserTools.from_repl(globals())
    result = tools.execute(request)
    print({RESULT_MARKER!r} + json.dumps({{"ok": True, "result": result}}, ensure_ascii=False))
except Exception as exc:
    print({RESULT_MARKER!r} + json.dumps({{"ok": False, "error_type": type(exc).__name__, "error": str(exc)}}, ensure_ascii=False))
'''
    try:
        completed = subprocess.run(
            [str(browser_use)], input=script, text=True, capture_output=True,
            check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Browser action timed out after {timeout}s"}
    except OSError as exc:
        return {"ok": False, "error": f"Could not launch browser-use: {exc}"}

    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(RESULT_MARKER):
            try:
                data = json.loads(line[len(RESULT_MARKER):])
            except json.JSONDecodeError:
                break
            if completed.returncode and data.get("ok"):
                return {"ok": False, "error": f"browser-use exited {completed.returncode}"}
            return data
    return {
        "ok": False,
        "error": "Browser Use returned no structured result; "
                 f"exit={completed.returncode}; "
                 f"stderr={completed.stderr[-350:]!r}; "
                 f"stdout={completed.stdout[-350:]!r}",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list_tabs", "read_tab", "read_json", "open_tab"))
    parser.add_argument("url", nargs="?", help="Exact existing tab URL (required except list_tabs)")
    args = parser.parse_args(argv)
    if (args.action == "list_tabs") != (args.url is None):
        parser.error("list_tabs takes no URL; other actions require a URL")
    request = {"action": args.action}
    if args.url is not None:
        request["url"] = args.url
    response = call_browser_action(request)
    print(json.dumps(response, indent=2, ensure_ascii=False))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
