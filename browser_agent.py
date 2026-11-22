
import os
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from browser_use import Agent, Browser, ChatGoogle

load_dotenv()


def main():
    # Get the free-tier API key
    api_key = os.getenv("GOOGLE_API_KEY_FREE")

    if not api_key:
        raise ValueError(
            "GOOGLE_API_KEY_FREE is missing from .env"
        )

    # Accept an arbitrary task
    task = input("\nWhat should AutoAgent do?\n> ").strip()

    if not task:
        print("No task provided.")
        return

    # Create a timestamped log file
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"browser_{timestamp}.log"

    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] "
            "%(name)s: %(message)s"
        )
    )

    logger = logging.getLogger("browser_use")
    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)

    try:
        print("\nStarting AutoAgent...")
        print("Model: gemini-3-flash-preview")
        print("API key: GOOGLE_API_KEY_FREE")
        print(f"Progress log: {log_path}\n")

        # Launch a local browser
        browser = Browser("chromium", headless=False)

        # Connect Gemini using the free key
        llm = ChatGoogle(
            model="gemini-3-flash-preview",
            api_key=api_key,
        )

        # Create the browser agent
        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
        )

        # Execute the task
        result = agent.run_sync(
            max_steps=15,
            timeout=300,
        )

        print("\n" + "=" * 50)
        print("AUTOAGENT RESULT")
        print("=" * 50)
        print(result.final_result())
        print(f"\nLog saved to: {log_path}")

    finally:
        logger.removeHandler(file_handler)
        file_handler.close()


if __name__ == "__main__":
    main()
