
import json
import time
import urllib.request

URL = "http://localhost:11434/api/chat"

tool = {
    "type": "function",
    "function": {
        "name": "new_tab",
        "description": "Open a new browser tab at a URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"}
            },
            "required": ["url"]
        }
    }
}

for thinking in (False, True):
    print(f"\n=== Thinking: {thinking} ===", flush=True)

    payload = {
        "model": "qwen3:8b",
        "stream": False,
        "think": thinking,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You control a browser using tools. "
                    "Call the provided tool to perform actions. "
                    "Do not write instructions or example code."
                )
            },
            {
                "role": "user",
                "content": "Open https://example.com in a new tab."
            }
        ],
        "tools": [tool],
        "options": {
            "num_ctx": 4096,
            "num_predict": 512,
            "temperature": 0
        },
        "keep_alive": "10m"
    }

    request = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}
    )

    start = time.perf_counter()

    with urllib.request.urlopen(
        request, timeout=180
    ) as response:
        result = json.load(response)

    elapsed = time.perf_counter() - start
    message = result.get("message", {})
    calls = message.get("tool_calls", [])

    print(f"Wall time: {elapsed:.2f}s")
    print(f"Tool calls: {json.dumps(calls, indent=2)}")
    print(f"Text response: {message.get('content', '')[:500]}")

    for name in (
        "load_duration",
        "prompt_eval_duration",
        "eval_duration",
    ):
        print(f"{name}: {result.get(name, 0)/1e9:.2f}s")

    print("Generated tokens:", result.get("eval_count"))

import json
import time
import urllib.request

URL = "http://localhost:11434/api/chat"

tool = {
    "type": "function",
    "function": {
        "name": "new_tab",
        "description": "Open a new browser tab at a URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"}
            },
            "required": ["url"]
        }
    }
}

for thinking in (False, True):
    print(f"\n=== Thinking: {thinking} ===", flush=True)

    payload = {
        "model": "qwen3:8b",
        "stream": False,
        "think": thinking,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You control a browser using tools. "
                    "Call the provided tool to perform actions. "
                    "Do not write instructions or example code."
                )
            },
            {
                "role": "user",
                "content": "Open https://example.com in a new tab."
            }
        ],
        "tools": [tool],
        "options": {
            "num_ctx": 4096,
            "num_predict": 512,
            "temperature": 0
        },
        "keep_alive": "10m"
    }

    request = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}
    )

    start = time.perf_counter()

    with urllib.request.urlopen(
        request, timeout=180
    ) as response:
        result = json.load(response)

    elapsed = time.perf_counter() - start
    message = result.get("message", {})
    calls = message.get("tool_calls", [])

    print(f"Wall time: {elapsed:.2f}s")
    print(f"Tool calls: {json.dumps(calls, indent=2)}")
    print(f"Text response: {message.get('content', '')[:500]}")

    for name in (
        "load_duration",
        "prompt_eval_duration",
        "eval_duration",
    ):
        print(f"{name}: {result.get(name, 0)/1e9:.2f}s")

    print("Generated tokens:", result.get("eval_count"))
