"""Offline tests: .venv/bin/python -m unittest -v test_browser_tools.py"""
import unittest

from browser_tools import BrowserToolError, BrowserTools, normalize_url


class FakeChrome:
    def __init__(self):
        self.tabs = [
            {"targetId": "uuid1", "url": "https://httpbin.org/uuid", "title": "JSON"},
            {"targetId": "ex1", "url": "https://example.com/", "title": "Example Domain"},
        ]
        self.pages = {
            "uuid1": {"url": "https://httpbin.org/uuid", "title": "🐴 JSON", "text": '{"uuid":"8d8d45ff-2639-40c7-8949-30911494af0d"}'},
            "ex1": {"url": "https://example.com/", "title": "🐴 Example Domain", "text": "Example Domain\nHello"},
        }
        self.current = "ex1"
        self.opens = 0
        self.switches = 0
        self.evaluations = 0

    def list_tabs(self, include_chrome=True):
        self.assert_include_chrome = include_chrome
        return list(self.tabs)

    def switch_tab(self, target):
        self.switches += 1
        self.current = target

    def js(self, script):
        self.evaluations += 1
        assert "location.href" in script
        return dict(self.pages[self.current])

    def new_tab(self, url):
        self.opens += 1
        tab_id = f"new{self.opens}"
        self.tabs.append({"targetId": tab_id, "url": url, "title": "New"})
        self.pages[tab_id] = {"url": url, "title": "🐴 New", "text": ""}
        self.current = tab_id
        return tab_id

    def tools(self):
        return BrowserTools(list_tabs=self.list_tabs, switch_tab=self.switch_tab,
                            js=self.js, new_tab=self.new_tab)


class BrowserToolsTests(unittest.TestCase):
    def setUp(self):
        self.chrome = FakeChrome()
        self.tools = self.chrome.tools()

    def test_url_normalization(self):
        self.assertEqual(normalize_url("https://EXAMPLE.com"), normalize_url("https://example.com/"))
        self.assertEqual(normalize_url("https://example.com/path/#hi"), normalize_url("https://example.com/path"))
        self.assertNotEqual(normalize_url("https://example.com/?x=1"), normalize_url("https://example.com/?x=2"))
        self.assertNotEqual(normalize_url("https://example.com/A"), normalize_url("https://example.com/a"))

    def test_reject_bad_urls(self):
        for url in ("file:///etc/passwd", "javascript:alert(1)", "https://", "https://user:pass@example.com"):
            with self.subTest(url=url), self.assertRaises(BrowserToolError):
                normalize_url(url)

    def test_list_does_not_switch_or_open(self):
        tabs = self.tools.list_tabs()
        self.assertEqual(len(tabs), 2)
        self.assertFalse(self.chrome.assert_include_chrome)
        self.assertEqual(self.chrome.opens, 0)
        self.assertEqual(self.chrome.switches, 0)

    def test_read_json_exact(self):
        result = self.tools.read_json("https://httpbin.org/uuid")
        self.assertEqual(result["data"]["uuid"], "8d8d45ff-2639-40c7-8949-30911494af0d")
        self.assertEqual(self.chrome.opens, 0)
        self.assertEqual(self.chrome.current, "uuid1")

    def test_read_tab_title_cleaned(self):
        result = self.tools.read_tab("https://example.com")
        self.assertEqual(result["title"], "Example Domain")
        self.assertIn("Hello", result["text"])
        self.assertFalse(result["text_truncated"])
        self.assertEqual(self.chrome.opens, 0)

    def test_read_missing_does_not_open(self):
        with self.assertRaisesRegex(BrowserToolError, "No existing"):
            self.tools.read_tab("https://missing.example.com")
        self.assertEqual(self.chrome.opens, 0)

    def test_duplicate_tab_fail_closed(self):
        self.chrome.tabs.append({"targetId": "ex2", "url": "https://example.com", "title": "Example Domain"})
        with self.assertRaisesRegex(BrowserToolError, "found 2"):
            self.tools.read_tab("https://example.com")
        with self.assertRaisesRegex(BrowserToolError, "2 matching"):
            self.tools.open_tab("https://example.com")
        self.assertEqual(self.chrome.opens, 0)

    def test_wrong_attachment_fails(self):
        self.chrome.pages["uuid1"]["url"] = "https://example.com"
        with self.assertRaisesRegex(BrowserToolError, "wrong page"):
            self.tools.read_json("https://httpbin.org/uuid")

    def test_invalid_json_fails(self):
        self.chrome.pages["uuid1"]["text"] = "not json"
        with self.assertRaisesRegex(BrowserToolError, "valid JSON"):
            self.tools.read_json("https://httpbin.org/uuid")

    def test_open_reuses_existing(self):
        result = self.tools.open_tab("https://example.com")
        self.assertFalse(result["created"])
        self.assertTrue(result["reused"])
        self.assertEqual(self.chrome.opens, 0)

    def test_open_new_url(self):
        result = self.tools.open_tab("https://new.example.com")
        self.assertTrue(result["created"])
        self.assertEqual(self.chrome.opens, 1)
        self.assertEqual(result["target_id"], "new1")

    def test_dispatch_rejects_freeform_code_and_extra_fields(self):
        for req in ({"action": "eval", "code": "alert(1)"},
                    {"action": "read_tab", "url": "https://example.com", "code": "delete"},
                    {"action": "list_tabs", "url": "https://example.com"}):
            with self.subTest(req=req), self.assertRaises(BrowserToolError):
                self.tools.execute(req)

    def test_dispatch_json(self):
        result = self.tools.execute({"action": "read_json", "url": "https://httpbin.org/uuid"})
        self.assertEqual(result["url"], "https://httpbin.org/uuid")

    def test_text_truncation(self):
        self.chrome.pages["ex1"]["text"] = "a" * 12005
        result = self.tools.read_tab("https://example.com")
        self.assertTrue(result["text_truncated"])
        self.assertEqual(len(result["text"]), 12000)

    def test_read_json_not_truncated(self):
        self.chrome.pages["uuid1"]["text"] = '{"data":"' + 'a' * 12020 + '"}'
        result = self.tools.read_json("https://httpbin.org/uuid")
        self.assertEqual(len(result["data"]["data"]), 12020)


if __name__ == "__main__":
    unittest.main()
