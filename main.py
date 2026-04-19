import asyncio
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from f5bot import login, reenable_keywords
from notify import send_notification
from gmail import fetch_new_threads


async def run() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        try:
            await login(page)
            reenabled = await reenable_keywords(page)
            print(f"Re-enabled {len(reenabled)} keyword(s): {reenabled}")
        finally:
            await browser.close()
    send_notification(reenabled)

    new_threads = fetch_new_threads()
    print(f"Found {len(new_threads)} new f5bot thread(s)")
    for t in new_threads:
        print(f"  {t['subject']}: {t['reddit_links']}")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(run())
