"""AutoAgent: existing Chrome + local Qwen + Ollama call timing.

Uses the same run_autoagent.py, monitor.py, and remote_monitor.py as before.
Prerequisites: Ollama running, qwen3-browser created, Chrome debugging enabled.
"""

import hashlib
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from browser_use import Agent, Browser, ChatOpenAI
from dotenv import load_dotenv
from ollama_timing_proxy import OllamaTimingProxy

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
MODEL = "qwen3-browser"
MAX_STEPS = 8
TIMEOUT_SECONDS = 300


def setup_file_logging(path: Path) -> logging.FileHandler:
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    for name in ("autoagent", "browser_use"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
    return handler


def write_model_trace(messages, path: Path) -> dict:
    """Summarize model outputs and tool feedback without storing their contents.

    Trace lines contain only part types, tool names, content lengths, and
    truncated SHA-256 fingerprints. No prompts or tool arguments are saved.
    """
    lines = []
    fingerprints = {}
    responses = 0
    tool_calls = 0
    feedback_parts = 0

    def fingerprint(value):
        try:
            encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')
        except Exception:
            encoded = repr(value).encode('utf-8', errors='replace')
        return len(encoded), hashlib.sha256(encoded).hexdigest()[:12]

    for message in messages:
        kind = type(message).__name__
        if kind == 'ModelResponse':
            responses += 1
            descriptions = []
            for part in getattr(message, 'parts', ()):
                part_type = type(part).__name__
                if part_type == 'ToolCallPart':
                    tool_calls += 1
                    name = str(getattr(part, 'tool_name', '?'))
                    length, digest = fingerprint(getattr(part, 'args', None))
                    descriptions.append(f'tool:{name} args_bytes={length} sha256={digest}')
                    key = f'tool:{name}:{digest}'
                elif part_type == 'TextPart':
                    length, digest = fingerprint(getattr(part, 'content', None))
                    descriptions.append(f'text bytes={length} sha256={digest}')
                    key = f'text:{digest}'
                else:
                    descriptions.append(part_type)
                    key = f'other:{part_type}'
                fingerprints[key] = fingerprints.get(key, 0) + 1
            lines.append(f'MODEL_RESPONSE {responses:02d} | ' + (' | '.join(descriptions) or '(no parts)'))
        elif kind == 'ModelRequest':
            for part in getattr(message, 'parts', ()):
                part_type = type(part).__name__
                if part_type not in ('ToolReturnPart', 'RetryPromptPart'):
                    continue
                feedback_parts += 1
                name = str(getattr(part, 'tool_name', '?'))
                content = getattr(part, 'content', None)
                length, digest = fingerprint(content)
                # Only indicate the presence of common failure markers; never log feedback text.
                lower = str(content).lower()
                error_flag = any(s in lower for s in ('traceback', 'nameerror', 'importerror', 'error:', 'failed'))
                lines.append(f'TOOL_FEEDBACK | type={part_type} tool={name} bytes={length} '
                             f'sha256={digest} error_marker={error_flag}')

    frequent = sorted(fingerprints.items(), key=lambda x: x[1], reverse=True)
    lines.append('')
    lines.append(f'SUMMARY | model_responses={responses} tool_calls={tool_calls} feedback_parts={feedback_parts}')
    for key, count in frequent[:5]:
        if count > 1:
            lines.append(f'REPEATED_OUTPUT | count={count} fingerprint={key}')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return { 'responses': responses, 'tool_calls': tool_calls, 'feedback_parts': feedback_parts,
             'repeated': [(key, count) for key, count in frequent if count > 1][:5] }


def main():
    load_dotenv(ROOT / ".env")
    print("\nWhat should AutoAgent do in Chrome?")
    objective = input("> ").strip()
    if not objective:
        print("No task entered.")
        return

    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = LOG_DIR / f"browser_{stamp}.log"
    handler = setup_file_logging(log_path)
    logger = logging.getLogger("autoagent")
    total_start = time.perf_counter()  # after input(); excludes typing and final Enter

    try:
        print(f"\nModel: {MODEL} (local Ollama only; no Gemini calls)")
        print("Browser: existing Google Chrome")
        print(f"Activity log: {log_path}")
        print("Model requests are timed and shown in the monitors.\n")
        logger.info("AUTOAGENT_STATUS | STARTING | Connecting to existing Chrome")

        # No paid model/provider is used here. Proxy is loopback-only.
        with OllamaTimingProxy() as proxy:
            print(f"Local model timing relay: {proxy.base_url}")
            logger.info("AUTOAGENT_STATUS | RUNNING | Local Ollama timing enabled")
            try:
                browser = Browser("chrome", keep_alive=True)
                llm = ChatOpenAI(
                    model=MODEL,
                    base_url=proxy.base_url,
                    api_key="ollama",
                    temperature=0,
                    reasoning_effort="none",
                )
                task = (
                    "Use an existing matching browser tab where practical; open a new tab only when needed. "
                    "You may read web pages and navigate websites. "
                    "Do not make purchases, send messages, change account settings, "
                    "or delete anything. If an action requires approval, stop. "
                    "If you encounter a CAPTCHA, stop and report it.\n\n"
                    f"Task: {objective}"
                )
                # Browser Use supplies these helpers as globals inside the agent's
                # persistent Python REPL. They are NOT importable from browser_harness.
                browser_instructions = (
                    "Browser execution rules for the Python tool:\n"
                    "- Browser functions new_tab(), page_info(), js(), and goto_url() "
                    "are already available directly in the Python tool's REPL.\n"
                    "- NEVER write `from browser_harness import ...`, "
                    "`import browser_harness`, or attempt to install browser_harness.\n"
                    "- For navigation call `new_tab('https://example.com')` "
                    "directly in the Python tool, without importing it.\n"
                    "- To inspect a page, call `print(page_info())` or "
                    "`print(js(\"document.querySelector('h1')?.innerText\"))`.\n"
                    "- Browser actions must be executed, not merely described. "
                    "Do not claim successful completion unless an action's result "
                    "has been observed through the Python tool.\n"
                    "- If a tool call fails, fix the code based on the error "
                    "and retry; do not invent page content."
                )
                # Add reliability guidance to the browser-use system prompt.
                # These are model instructions, not enforced execution safeguards.
                browser_instructions += """

Additional browser reliability rules:

1. Before opening a new tab, inspect existing tabs when
   appropriate. Do not open duplicate tabs for retries.

2. If extracting an element fails, stay in the same tab.
   Inspect the page and try another extraction method.

3. For JSON responses, read document.body.innerText
   rather than guessing CSS selectors.

4. Do not repeat an identical failed action more than once
   without changing your approach.

5. Verify retrieved information against the actual page
   before finishing.

6. Never report a task as completed if required information
   has not been retrieved.
"""
                agent = Agent(
                    task=task,
                    llm=llm,
                    browser=browser,
                    judge=False,
                    extend_system_message=browser_instructions,
                )
                logger.info("AUTOAGENT_STATUS | RUNNING | Browser-use execution started")
                result = agent.run_sync(max_steps=MAX_STEPS, timeout=TIMEOUT_SECONDS)
                trace_path = LOG_DIR / f"trace_{stamp}.txt"
                trace = write_model_trace(result.messages, trace_path)
                print(f"\nAgent-reported success: {result.success} (not independent verification)")
                print(f"Steps used: {result.number_of_steps()}/{MAX_STEPS}")
                if result.number_of_steps() >= MAX_STEPS:
                    print("WARNING: Reached step limit; completion may be only final-answer delivery.")
                print(f"Model-response trace: {trace_path}")
                print(f"Trace totals: {trace['responses']} responses, {trace['tool_calls']} tool calls")
                for key, count in trace['repeated']:
                    print(f"Repeated {count}x: {key}")
                print("\n" + "=" * 55)
                print(f"AUTOAGENT RESULT — {result.status}")
                print("=" * 55)
                print(result.final_result() or result.partial or result.error or "No result returned.")
                if result.status == "error":
                    logger.error("AUTOAGENT_STATUS | ERROR | %s", result.error)
                else:
                    logger.info("AUTOAGENT_STATUS | FINISHED | Reported: %s", result.status)
            finally:
                elapsed = time.perf_counter() - total_start
                metrics = proxy.summary()
                other = max(0, elapsed - metrics["total_model_seconds"])
                logger.info(
                    "AUTOAGENT_TIMING | total=%.2fs | model_requests=%s | "
                    "model=%.2fs | outside_model=%.2fs",
                    elapsed, metrics["calls"], metrics["total_model_seconds"], other
                )
                print("\n" + "=" * 55)
                print("TIMING SUMMARY")
                print("=" * 55)
                print(f"Total elapsed:          {elapsed:.2f}s")
                print(f"Ollama requests:        {metrics['calls']}")
                print(f"Ollama request time:    {metrics['total_model_seconds']:.2f}s")
                print(f"Time outside requests:  {other:.2f}s")
                print("Outside requests includes browser-use setup, execution, and browser waits.")
                guard_stats = metrics.get("guard", {})
                print(
                    f"Guard: {guard_stats.get('rewritten_calls', 0)} intercepted new_tab reference(s), "
                    f"{guard_stats.get('blocked_repeats', 0)} repeated cell(s) blocked, "
                    f"{guard_stats.get('unscreened_calls', 0)} unscreened cell(s), "
                    f"{guard_stats.get('repaired_imports', 0)} repaired import(s)"
                )
                logger.info("AUTOAGENT_GUARD | %s", guard_stats)
                print("Individual request timings: see your monitor/log file.")
                print(f"Activity log: {log_path}")

    except KeyboardInterrupt:
        logger.warning("AUTOAGENT_STATUS | INTERRUPTED | Stopped by user")
        print("\nStopped by user.")
    except Exception:
        logger.exception("AUTOAGENT_STATUS | ERROR | Unexpected error")
        raise
    finally:
        for name in ("autoagent", "browser_use"):
            logging.getLogger(name).removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    main()
