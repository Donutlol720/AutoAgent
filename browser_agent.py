"""AutoAgent: run browser tasks in the Chrome you already have open.

Requires browser-use==1.0.8rc1, python-dotenv, and Chrome remote debugging:
chrome://inspect/#remote-debugging -> Allow remote debugging.
"""

import logging
from datetime import datetime
from pathlib import Path

from browser_use import Agent, Browser, ChatOpenAI
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
MODEL = "qwen3:8b"
MAX_STEPS = 15
TIMEOUT_SECONDS = 300


def setup_file_logging(log_path: Path) -> logging.FileHandler:
    """Capture browser-use activity and our own status messages in one file."""
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    for name in ("browser_use", "autoagent"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
    return handler


def main() -> None:
    load_dotenv(BASE_DIR / ".env")

    print("\nWhat should AutoAgent do in Chrome?")
    objective = input("> ").strip()
    if not objective:
        print("No task entered.")
        return

    LOG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = LOG_DIR / f"browser_{timestamp}.log"
    handler = setup_file_logging(log_path)
    logger = logging.getLogger("autoagent")

    try:
        logger.info("AUTOAGENT_STATUS | STARTING | Connecting to existing Chrome")
        print(f"\nModel: {MODEL} (local Ollama)")
        print("Browser: existing Google Chrome")
        print(f"Activity log: {log_path}")
        print("Open 'python monitor.py' in another terminal to watch progress.\n")

        # Attaches to your already-running Chrome. It does NOT start a blank Chromium.
        # First enable chrome://inspect/#remote-debugging in that Chrome window.
        browser = Browser("chrome", keep_alive=True)
        llm = ChatOpenAI(
            model=MODEL,
            base_url="http://localhost:11434/v1",
            api_key="ollama",
        )

        # These are instructions to the model, not hard permission boundaries.
        task = (
            "Use a new browser tab for this task where practical. "
            "You may read web pages and navigate websites. "
            "Do not make purchases, send messages, change account settings, "
            "or delete anything. If such an action is necessary, stop and "
            "explain what approval is needed. "
            "If you encounter a CAPTCHA, stop and report it rather than "
            "trying to bypass it.\n\n"
            f"Task: {objective}"
        )

        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
            judge=False,  # Avoid an additional LLM call after the main task.
        )

        logger.info("AUTOAGENT_STATUS | RUNNING | Task started")
        result = agent.run_sync(max_steps=MAX_STEPS, timeout=TIMEOUT_SECONDS)

        print("\n" + "=" * 55)
        print(f"AUTOAGENT RESULT — {result.status}")
        print("=" * 55)
        print(result.final_result() or result.partial or result.error or "No result returned.")
        print(f"\nActivity log: {log_path}")

        if result.status == "error":
            logger.error("AUTOAGENT_STATUS | ERROR | %s", result.error)
            print("\nIf Chrome could not connect, enable remote debugging at:")
            print("chrome://inspect/#remote-debugging")
        else:
            logger.info("AUTOAGENT_STATUS | FINISHED | Status: %s", result.status)

    except KeyboardInterrupt:
        logger.warning("AUTOAGENT_STATUS | INTERRUPTED | Stopped by user")
        print("\nStopped by user.")
    except Exception:
        logger.exception("AUTOAGENT_STATUS | ERROR | Unexpected error")
        raise
    finally:
        for name in ("browser_use", "autoagent"):
            logging.getLogger(name).removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    main()
