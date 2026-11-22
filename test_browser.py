
from browser_use import Agent, Browser, ChatGoogle
from dotenv import load_dotenv

load_dotenv()

def main():
    browser = Browser("chromium", headless=False)

    llm = ChatGoogle(model="gemini-3-flash-preview")

    agent = Agent(
        task=(
            "Go to https://en.wikipedia.org/wiki/Python_(programming_language) "
            "and find the year Python was first released. "
            "Report the year and the source."
        ),
        llm=llm,
        browser=browser,
    )

    result = agent.run_sync()
    print("\nFinal result:", result.final_result())


if __name__ == "__main__":
    main()
