"""Offline structured-agent tests; no Chrome or Ollama required."""
import unittest
import json
from unittest.mock import patch

from structured_browser_agent import (
    target_urls, decide_allowed_action, validated_read, run_task, model_decision,
)
from browser_tools import normalize_url

URL1 = "https://httpbin.org/uuid"
URL2 = "https://example.com"
UUID = "8d8d45ff-2639-40c7-8949-30911494af0d"


class FakeModel:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.calls = 0

    def __call__(self, messages):
        self.calls += 1
        return next(self.actions)


def fake_execute(req):
    if req["action"] == "list_tabs":
        return {"ok": True, "result": [
            {"url": URL1, "title": "JSON", "target_id": "a"},
            {"url": URL2, "title": "Example Domain", "target_id": "b"},
            {"url": "https://mail.google.com", "title": "Private", "target_id": "c"},
        ]}
    if req["url"] == URL1:
        if req["action"] == "read_json":
            return {"ok": True, "result": {"url": URL1, "target_id": "a", "data": {"uuid": UUID}}}
        return {"ok": True, "result": {"url": URL1, "target_id": "a", "title": "JSON", "text": '{"uuid":"' + UUID + '"}'}}
    if req["url"] == URL2:
        return {"ok": True, "result": {"url": "https://example.com/", "target_id": "b",
                                        "title": "Example Domain", "text": "Hello"}}
    raise ValueError("Unexpected request " + str(req))


class StructuredTests(unittest.TestCase):

    def test_ollama_request_is_structured_and_nonstreaming(self):
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps({"choices": [{"message": {"content": json.dumps({"action": "read_json", "url": URL1})}}]}).encode()
        captured = []
        def fake_urlopen(request, timeout):
            captured.append({"url": request.full_url, "payload": json.loads(request.data)})
            return FakeResponse()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            decision = model_decision("http://127.0.0.1:9999/v1", [{"role": "user", "content": "Read"}])
        self.assertEqual(decision, {"action": "read_json", "url": URL1})
        self.assertEqual(captured[0]["url"], "http://127.0.0.1:9999/v1/chat/completions")
        self.assertEqual(captured[0]["payload"]["reasoning_effort"], "none")
        self.assertEqual(captured[0]["payload"]["response_format"], {"type": "json_object"})
        self.assertFalse(captured[0]["payload"]["stream"])

    def test_url_targets(self):
        self.assertEqual(target_urls("Use https://EXAMPLE.com/ and https://example.com and https://httpbin.org/uuid."),
                         ["https://EXAMPLE.com/", URL1])

    def test_disallow_code(self):
        allowed = {normalize_url(URL1): URL1}
        for request in ({"action": "python", "code": "print(1)"},
                        {"action": "open_tab", "url": URL1},
                        {"action": "read_json", "url": "https://mail.google.com"},
                        {"action": "read_json", "url": URL1, "code": "hi"}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                decide_allowed_action(request, allowed)

    def test_success_two_sources(self):
        model = FakeModel([{"action": "read_json", "url": URL1},
                           {"action": "read_tab", "url": URL2}])
        out = run_task(f"Read {URL1} and {URL2}", decide=model, execute=fake_execute)
        self.assertTrue(out["verified"])
        self.assertEqual(len(out["observations"]), 2)
        self.assertEqual(out["observations"][URL1]["result"]["data"]["uuid"], UUID)
        self.assertEqual(model.calls, 2)

    def test_premature_finish_refused(self):
        model = FakeModel([{"action": "finish"}, {"action": "read_tab", "url": URL2}])
        out = run_task(f"Read {URL2}", decide=model, execute=fake_execute)
        self.assertTrue(out["verified"])
        self.assertEqual(out["steps"], 2)

    def test_cannot_read_unapproved_tab(self):
        model = FakeModel([{"action": "read_tab", "url": "https://mail.google.com"}])
        out = run_task(f"Read {URL2}", decide=model, execute=fake_execute, max_steps=1)
        self.assertFalse(out["verified"])
        self.assertEqual(out["observations"], {})

    def test_wrong_source_fails_verification(self):
        item = {"ok": True, "result": {"url": URL2, "data": {"uuid": UUID}}}
        with self.assertRaisesRegex(ValueError, "different URL"):
            validated_read("read_json", URL1, item)

    def test_faked_model_answer_does_not_count(self):
        model = FakeModel([{"action": "finish", "answer": "All done"},
                           {"action": "read_tab", "url": URL2}])
        out = run_task(f"Read {URL2}", decide=model, execute=fake_execute, max_steps=2)
        self.assertTrue(out["verified"])
        self.assertEqual(model.calls, 2)

    def test_browser_failure_is_incomplete(self):
        model = FakeModel([{"action": "read_json", "url": URL1},
                           {"action": "read_json", "url": URL1}])
        out = run_task(f"Read {URL1}", decide=model,
                       execute=lambda r: {"ok": False, "error": "No matching tab"}, max_steps=2)
        self.assertFalse(out["verified"])
        self.assertEqual(len(out["errors"]), 2)

    def test_list_tabs_filters_private_urls(self):
        states = []
        def decide(msgs):
            states.append(str(msgs))
            return [{"action": "list_tabs"}, {"action": "read_json", "url": URL1}][len(states)-1]
        out = run_task(f"Read {URL1}", decide=decide, execute=fake_execute)
        self.assertTrue(out["verified"])
        self.assertNotIn("mail.google.com", states[1])

    def test_no_explicit_url_returns_no_model_call(self):
        model = FakeModel([])
        out = run_task("Read the webpage", decide=model, execute=fake_execute)
        self.assertFalse(out["verified"])
        self.assertEqual(model.calls, 0)

    def test_too_many_retries_blocked(self):
        executed = []
        def execute(req):
            executed.append(req)
            return {"ok": False, "error": "No matching tab"}
        model = FakeModel([{"action": "read_json", "url": URL1}] * 4)
        out = run_task(f"Read {URL1}", decide=model, execute=execute, max_steps=4)
        self.assertFalse(out["verified"])
        self.assertEqual(len(executed), 2)


if __name__ == "__main__":
    unittest.main()
