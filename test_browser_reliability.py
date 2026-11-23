"""Run offline: .venv/bin/python -m unittest -v test_browser_reliability.py"""
import json
import unittest
from browser_reliability import BrowserActionGuard, guard_code, rewrite_code


def mock_browser():
    state = {"tabs": [], "opens": 0, "switches": 0}
    def list_tabs(include_chrome=False):
        return list(state["tabs"])
    def switch_tab(target):
        state["switches"] += 1
        assert isinstance(target, str)
    def new_tab(url):
        state["opens"] += 1
        tid = str(state["opens"])
        state["tabs"].append({"url": url, "targetId": tid})
        return tid
    env = {
        "list_tabs": list_tabs, "switch_tab": switch_tab, "new_tab": new_tab,
        "js": lambda x: "ok", "page_info": lambda: {"url": "https://example.com"},
    }
    return state, env


def model_response(code):
    return json.dumps({"choices": [{"message": {"role": "assistant", "tool_calls": [{
        "id": "call_1", "type": "function", "function": {
            "name": "python", "arguments": json.dumps({"code": code})}
    }]}, "finish_reason": "tool_calls"}]}).encode()


def get_code(raw):
    return json.loads(json.loads(raw)["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])["code"]


class GuardTests(unittest.TestCase):
    def test_reuse_direct_new_tab(self):
        code, count = guard_code("new_tab('https://example.com')")
        self.assertEqual(count, 1)
        state, env = mock_browser()
        exec(code, env)
        exec(code, env)
        self.assertEqual(state["opens"], 1)
        self.assertEqual(state["switches"], 1)

    def test_invalid_import_removed_and_tab_reused(self):
        code, nav, repaired = rewrite_code("from browser_harness import new_tab, list_tabs, page_info, js\nnew_tab('https://example.com')\nprint(page_info())")
        self.assertEqual((nav, repaired), (1, 1))
        self.assertNotIn("from browser_harness import", code)
        state, env = mock_browser()
        exec(code, env)
        exec(code, env)
        self.assertEqual(state["opens"], 1)

    def test_import_only_repaired(self):
        source = "from browser_harness import page_info, js\nprint(page_info())"
        code, nav, repaired = rewrite_code(source)
        self.assertEqual((nav, repaired), (0, 1))
        _, env = mock_browser()
        exec(code, env)

    def test_module_import_alias(self):
        code, nav, repaired = rewrite_code("import browser_harness as bh\nbh.new_tab('https://example.com')\nprint(bh.page_info())")
        self.assertEqual((nav, repaired), (1, 1))
        state, env = mock_browser()
        exec(code, env)
        self.assertEqual(state["opens"], 1)

    def test_imported_new_tab_alias(self):
        code, nav, repaired = rewrite_code("from browser_harness import new_tab as open_new\nopen_new('https://example.com')")
        self.assertEqual((nav, repaired), (1, 1))
        state, env = mock_browser()
        exec(code, env)
        exec(code, env)
        self.assertEqual(state["opens"], 1)

    def test_no_overeager_import_removal(self):
        original = "from browser_harness import some_other_name\nprint('x')"
        code, nav, repaired = rewrite_code(original)
        self.assertEqual((code, nav, repaired), (original, 0, 0))

    def test_two_different_tabs(self):
        code, nav, _ = rewrite_code("new_tab('https://example.com'); new_tab('https://httpbin.org/uuid')")
        state, env = mock_browser()
        exec(code, env)
        self.assertEqual((nav, state["opens"]), (2, 2))

    def test_response_filter(self):
        guard = BrowserActionGuard()
        raw, notices = guard.filter_chat_completion(model_response("from browser_harness import new_tab\nnew_tab('https://example.com')"))
        self.assertIn("__autoagent_reuse_or_open_tab", get_code(raw))
        self.assertEqual(guard.summary()["repaired_imports"], 1)
        self.assertTrue(any(n.startswith("REPAIRED") for n in notices))

    def test_third_repeated_code_blocked(self):
        guard = BrowserActionGuard()
        original = model_response("new_tab('https://example.com')")
        guard.filter_chat_completion(original)
        guard.filter_chat_completion(original)
        raw, _ = guard.filter_chat_completion(original)
        self.assertIn("Repeated identical Python action blocked", get_code(raw))
        self.assertEqual(guard.summary()["blocked_repeats"], 1)

    def test_unrelated_code_unmodified(self):
        source = "print(page_info())"
        guard = BrowserActionGuard()
        original = model_response(source)
        changed, _ = guard.filter_chat_completion(original)
        self.assertEqual(original, changed)

    def test_bad_python_unmodified(self):
        source = "new_tab('unterminated"
        code, nav, repaired = rewrite_code(source)
        self.assertEqual((code, nav, repaired), (source, 0, 0))


if __name__ == "__main__":
    unittest.main()
