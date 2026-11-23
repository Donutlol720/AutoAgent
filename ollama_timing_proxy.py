"""Local, stdlib-only timing relay for Ollama's OpenAI-compatible API.

Browser-use -> 127.0.0.1:ephemeral_port -> Ollama at 127.0.0.1:11434.
Only timings and byte counts are logged, never request/response bodies.
No LAN listener, external service, or extra dependency is needed.
"""

import http.client
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from browser_reliability import BrowserActionGuard

LOG = logging.getLogger("autoagent")


def _model_response_shape(raw: bytes, is_stream: bool) -> str:
    """Summarize an Ollama response without logging its contents or arguments.

    For debugging empty browser-use model responses, report whether Ollama
    produced visible text, hidden reasoning, or structured tool calls.
    """
    content_chars = 0
    reasoning_chars = 0
    tool_calls = 0
    finish_reasons = []
    parsed_events = 0
    truncated = False

    def text_len(value):
        if isinstance(value, str):
            return len(value)
        if isinstance(value, list):
            return sum(text_len(item.get("text", "")) for item in value if isinstance(item, dict))
        return 0

    def ingest(event, streamed=False):
        nonlocal content_chars, reasoning_chars, tool_calls, parsed_events
        if not isinstance(event, dict):
            return
        parsed_events += 1
        choices = event.get("choices", [])
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            msg = choice.get("delta" if streamed else "message") or {}
            if not isinstance(msg, dict):
                continue
            content_chars += text_len(msg.get("content"))
            reasoning_chars += text_len(msg.get("reasoning"))
            reasoning_chars += text_len(msg.get("reasoning_content"))
            calls = msg.get("tool_calls") or []
            if isinstance(calls, list):
                if streamed:
                    # Count uniquely indexed tool call fragments, not every chunk.
                    for call in calls:
                        if isinstance(call, dict):
                            tool_indices.add(call.get("index", 0))
                else:
                    tool_calls += len(calls)
            if choice.get("finish_reason") is not None:
                finish_reasons.append(str(choice["finish_reason"]))

    tool_indices = set()
    try:
        if is_stream:
            for line in raw.splitlines():
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                event_data = line[5:].strip()
                if event_data == b"[DONE]":
                    continue
                try:
                    ingest(json.loads(event_data), streamed=True)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    truncated = True
            tool_calls = len(tool_indices)
        else:
            ingest(json.loads(raw), streamed=False)
    except (ValueError, UnicodeDecodeError):
        return "format=unparseable"

    markers = ",".join(sorted(set(finish_reasons))) or "missing"
    empty = content_chars == 0 and tool_calls == 0
    return (
        f"format={'stream' if is_stream else 'json'} "
        f"events={parsed_events} text_chars={content_chars} "
        f"reasoning_chars={reasoning_chars} tool_calls={tool_calls} "
        f"finish_reason={markers} empty_visible_output={empty} "
        f"partial_capture={truncated}"
    )


class OllamaTimingProxy:
    def __init__(self, upstream_host="127.0.0.1", upstream_port=11434):
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self._lock = threading.Lock()
        self._records = []
        self._next_id = 0
        self.guard = BrowserActionGuard()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format_string, *args):
                # Avoid logging URLs, headers, or sensitive request contents.
                pass

            def do_GET(self):
                self._forward()

            def do_POST(self):
                self._forward()

            def _reply_error(self, status, message):
                payload = json.dumps({"error": {"message": message, "type": "proxy_error"}}).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True

            def _forward(self):
                route = urlsplit(self.path).path
                if not route.startswith("/v1/"):
                    self._reply_error(404, "Only Ollama /v1/ endpoints are supported")
                    return

                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._reply_error(400, "Invalid Content-Length")
                    return
                if size < 0 or size > 20_000_000:
                    self._reply_error(413, "Request too large")
                    return
                if self.headers.get("Transfer-Encoding"):
                    self._reply_error(411, "Chunked request bodies are not supported")
                    return
                body = self.rfile.read(size) if size else b""
                is_model_call = self.command == "POST" and route in (
                    "/v1/chat/completions", "/v1/responses"
                )

                with proxy._lock:
                    proxy._next_id += 1
                    request_id = proxy._next_id

                if is_model_call:
                    try:
                        request_details = json.loads(body)
                        settings = (
                            f"stream={bool(request_details.get('stream', False))} "
                            f"reasoning_effort={request_details.get('reasoning_effort', 'absent')}"
                        )
                    except (ValueError, UnicodeDecodeError):
                        settings = "settings=unavailable"
                    LOG.info(
                        "MODEL_CALL | START | #%s %s | request=%s bytes | %s",
                        request_id, route, len(body), settings
                    )

                start = time.perf_counter()
                first_byte_at = None
                bytes_out = 0
                captured = bytearray()
                capture_limit = 524288  # max 512 KiB kept temporarily; never written to a file
                stream_response = False
                status = None
                error = None
                upstream = http.client.HTTPConnection(
                    proxy.upstream_host, proxy.upstream_port, timeout=360
                )
                try:
                    headers = {"Accept-Encoding": "identity"}
                    for key in ("Content-Type", "Authorization", "Accept", "OpenAI-Beta"):
                        value = self.headers.get(key)
                        if value:
                            headers[key] = value
                    upstream.request(self.command, self.path, body=body if size else None,
                                     headers=headers)
                    response = upstream.getresponse()
                    status = response.status
                    stream_response = 'text/event-stream' in (response.getheader('Content-Type', '') or '').lower()
                    # Buffer only non-streaming chat completions so a duplicate
                    # new_tab tool call can be blocked BEFORE REPL execution.
                    # The user's browser-use requests have stream=False.
                    guarded = is_model_call and route == "/v1/chat/completions" and status == 200
                    max_guard_bytes = 2_000_000
                    if guarded:
                        if stream_response:
                            # Fail closed instead of silently bypassing the guard.
                            self._reply_error(502, "Guard requires non-streaming chat completions")
                            bytes_out = 0
                            error = "Guard blocked streaming tool response"
                        else:
                            chunks = []
                            total = 0
                            while True:
                                chunk = response.read1(8192)
                                if not chunk:
                                    break
                                if first_byte_at is None:
                                    first_byte_at = time.perf_counter()
                                total += len(chunk)
                                if total > max_guard_bytes:
                                    raise ValueError("Model response exceeds guard inspection limit")
                                chunks.append(chunk)
                            original = b"".join(chunks)
                            forwarded, notices = proxy.guard.filter_chat_completion(original)
                            for notice in notices:
                                LOG.warning("BROWSER_GUARD | %s", notice)
                            self.send_response(status)
                            self.send_header("Content-Type", "application/json")
                            self.send_header("Content-Length", str(len(forwarded)))
                            self.send_header("Connection", "close")
                            self.end_headers()
                            self.close_connection = True
                            self.wfile.write(forwarded)
                            self.wfile.flush()
                            bytes_out = len(forwarded)
                            captured.extend(forwarded[:capture_limit])
                    else:
                        self.send_response(status)
                        hop_headers = {
                            "connection", "keep-alive", "proxy-authenticate",
                            "proxy-authorization", "te", "trailer", "transfer-encoding",
                            "upgrade", "content-length"
                        }
                        for key, val in response.getheaders():
                            if key.lower() not in hop_headers:
                                self.send_header(key, val)
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.close_connection = True
                        while True:
                            chunk = response.read1(8192)
                            if not chunk:
                                break
                            if first_byte_at is None:
                                first_byte_at = time.perf_counter()
                            self.wfile.write(chunk)
                            self.wfile.flush()
                            bytes_out += len(chunk)
                            if is_model_call and len(captured) < capture_limit:
                                captured.extend(chunk[:capture_limit - len(captured)])
                except (BrokenPipeError, ConnectionResetError) as exc:
                    error = f"Client disconnected: {type(exc).__name__}"
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    if status is None:
                        self._reply_error(502, f"Cannot reach Ollama: {error}")
                        status = 502
                finally:
                    upstream.close()
                    elapsed = time.perf_counter() - start
                    first_byte = (first_byte_at - start) if first_byte_at else None
                    if is_model_call:
                        record = {
                            "id": request_id, "route": route, "status": status,
                            "seconds": elapsed, "first_byte_seconds": first_byte,
                            "request_bytes": len(body), "response_bytes": bytes_out,
                            "error": error
                        }
                        with proxy._lock:
                            proxy._records.append(record)
                        LOG.info(
                            "MODEL_CALL | DONE | #%s status=%s total=%.2fs "
                            "first_byte=%s response=%s bytes%s",
                            request_id, status, elapsed,
                            f"{first_byte:.2f}s" if first_byte is not None else "n/a",
                            bytes_out, f" error={error}" if error else ""
                        )
                        if route == '/v1/chat/completions':
                            shape = _model_response_shape(bytes(captured), stream_response)
                            LOG.info(
                                "MODEL_RESPONSE_SHAPE | #%s %s capture_truncated=%s",
                                request_id, shape, bytes_out > capture_limit
                            )

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="ollama-timing", daemon=True
        )

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def summary(self):
        with self._lock:
            records = list(self._records)
        return {
            "calls": len(records),
            "total_model_seconds": sum(r["seconds"] for r in records),
            "records": records,
            "guard": self.guard.summary(),
        }
