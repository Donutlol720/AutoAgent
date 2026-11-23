"""Guard model-generated browser-harness REPL code (browser-use 1.0).

The browser-harness functions are PRE-IMPORTED in the agent REPL, NOT
importable from the browser_harness package. Remove invalid helper imports,
rewrite direct new_tab() calls to reuse existing tabs, and block a third
identical Python tool cell. This is a reliability aid, NOT a security boundary.

Only non-streaming OpenAI-compatible `python` tool calls are inspected.
"""

import ast
import hashlib
import json


# Documented browser-harness helpers made available inside browser-use's REPL.
REPL_HELPERS = frozenset({
    "new_tab", "goto_url", "wait_for_load", "page_info", "js",
    "list_tabs", "current_tab", "switch_tab", "close_tab",
    "ensure_real_tab", "activate_tab", "cdp", "iframe_target",
})

# This is added before the model's code, in the same agent REPL cell.
# Browser-harness already supplies list_tabs(), switch_tab(), new_tab().
GUARDED_TAB_HELPER = '''
def __autoagent_reuse_or_open_tab(url="about:blank"):
    if not isinstance(url, str):
        raise TypeError("new_tab URL must be a string")
    # No fallback to a new tab if enumeration fails: avoid accidental duplicates.
    from urllib.parse import urlsplit
    def __aa_normalize(s):
        u = urlsplit(s)
        path = u.path or "/"
        return (u.scheme.lower(), u.netloc.lower(), path, u.query)
    if url != "about:blank":
        for __aa_tab in list_tabs(include_chrome=False):
            if __aa_normalize(__aa_tab.get("url") or "") == __aa_normalize(url):
                __aa_id = __aa_tab.get("targetId") or __aa_tab.get("target_id")
                if not __aa_id:
                    raise RuntimeError("Matching tab lacks a targetId")
                switch_tab(__aa_id)
                print("AUTOAGENT_GUARD: Reused existing tab; no duplicate created")
                return __aa_id
    return new_tab(url)
'''


class _HarnessRewriter(ast.NodeTransformer):
    """Repair invalid browser_harness imports and intercept tab creation."""

    def __init__(self):
        self.new_tab_calls = 0
        self.imports_removed = 0
        self.module_aliases = set()
        self.needs_wrapper = False

    def visit_ImportFrom(self, node):
        if node.module != "browser_harness":
            return node
        # Do not silently suppress unknown imports or wildcard imports.
        if any(a.name not in REPL_HELPERS for a in node.names):
            return node
        self.imports_removed += 1
        assignments = []
        for alias in node.names:
            if alias.asname and alias.asname != alias.name:
                # Restore aliases without importing the missing module exports.
                source = "__autoagent_reuse_or_open_tab" if alias.name == "new_tab" else alias.name
                assignments.append(ast.Assign(
                    targets=[ast.Name(id=alias.asname, ctx=ast.Store())],
                    value=ast.Name(id=source, ctx=ast.Load()),
                ))
                if alias.name == "new_tab":
                    self.needs_wrapper = True
                    self.new_tab_calls += 1  # possible navigation via aliased helper
        return assignments or None

    def visit_Import(self, node):
        remaining = []
        for alias in node.names:
            if alias.name == "browser_harness":
                self.module_aliases.add(alias.asname or "browser_harness")
                self.imports_removed += 1
            else:
                remaining.append(alias)
        if not remaining:
            return None
        node.names = remaining
        return node

    def visit_Attribute(self, node):
        self.generic_visit(node)
        if (isinstance(node.value, ast.Name)
                and node.value.id in self.module_aliases
                and node.attr in REPL_HELPERS):
            if node.attr == "new_tab":
                self.needs_wrapper = True
                self.new_tab_calls += 1
                name = "__autoagent_reuse_or_open_tab"
            else:
                name = node.attr
            return ast.copy_location(ast.Name(id=name, ctx=node.ctx), node)
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "new_tab":
            self.new_tab_calls += 1
            self.needs_wrapper = True
            node.func = ast.copy_location(
                ast.Name(id="__autoagent_reuse_or_open_tab", ctx=ast.Load()), node.func
            )
        return node


def rewrite_code(code: str) -> tuple[str, int, int]:
    """Return (rewritten code, intercepted navigation count, repaired imports)."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return code, 0, 0
    rewriter = _HarnessRewriter()
    tree = rewriter.visit(tree)
    if not rewriter.new_tab_calls and not rewriter.imports_removed:
        return code, 0, 0
    ast.fix_missing_locations(tree)
    replacement = ast.unparse(tree)
    if rewriter.needs_wrapper:
        replacement = GUARDED_TAB_HELPER + "\n" + replacement
    return replacement, rewriter.new_tab_calls, rewriter.imports_removed


def guard_code(code: str) -> tuple[str, int]:
    """Backward-compatible interface: return (rewritten code, nav count)."""
    replacement, count, _ = rewrite_code(code)
    return replacement, count


class BrowserActionGuard:
    def __init__(self):
        self.rewritten_calls = 0
        self.blocked_repeats = 0
        self.unscreened_calls = 0
        self.repaired_imports = 0
        self._last_code_digest = None
        self._consecutive = 0

    @staticmethod
    def _tool_code(tool: dict):
        if not isinstance(tool, dict) or tool.get("function", {}).get("name") != "python":
            return None
        original = tool["function"].get("arguments")
        try:
            arguments = json.loads(original) if isinstance(original, str) else original
        except (ValueError, TypeError):
            return None
        if not isinstance(arguments, dict):
            return None
        for field in ("code", "source"):
            if isinstance(arguments.get(field), str):
                return arguments, field, arguments[field]
        return None

    def filter_chat_completion(self, raw: bytes) -> tuple[bytes, list[str]]:
        """Inspect non-streaming OpenAI-format tool output *before* REPL execution."""
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return raw, []
        if not isinstance(payload, dict):
            return raw, []
        changed = False
        notices = []
        for choice in payload.get("choices", []):
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            for tool in message.get("tool_calls") or []:
                decoded = self._tool_code(tool)
                if decoded is None:
                    continue
                arguments, field, code = decoded
                digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
                if digest == self._last_code_digest:
                    self._consecutive += 1
                else:
                    self._last_code_digest = digest
                    self._consecutive = 1
                if self._consecutive >= 3:
                    self.blocked_repeats += 1
                    new_code = (
                        "print('AUTOAGENT_GUARD: Repeated identical Python action blocked. "
                        "Inspect the last tool output and change your approach.')"
                    )
                    notices.append("BLOCKED: repeated identical Python cell")
                else:
                    new_code, nav_count, repaired_count = rewrite_code(code)
                    if repaired_count:
                        self.repaired_imports += repaired_count
                        notices.append(f"REPAIRED: {repaired_count} invalid browser_harness import(s)")
                    if nav_count:
                        self.rewritten_calls += nav_count
                        notices.append(f"GUARDED: {nav_count} new_tab reference(s)")
                    elif "new_tab" in code and new_code == code:
                        self.unscreened_calls += 1
                        notices.append("UNSCREENED: new_tab reference not intercepted")
                if new_code == code:
                    continue
                arguments[field] = new_code
                old = tool["function"]["arguments"]
                tool["function"]["arguments"] = (
                    json.dumps(arguments, ensure_ascii=False)
                    if isinstance(old, str) else arguments
                )
                changed = True
        if changed:
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), notices
        return raw, notices

    def summary(self):
        return {
            "rewritten_calls": self.rewritten_calls,
            "blocked_repeats": self.blocked_repeats,
            "unscreened_calls": self.unscreened_calls,
            "repaired_imports": self.repaired_imports,
        }
