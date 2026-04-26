from dotenv import load_dotenv
load_dotenv()

import asyncio
import re
from playwright.async_api import async_playwright
from f5bot import login, reenable_keywords
from notify import send_notification, send_post
from gmail import fetch_new_threads, save_seen
from analyze import run_analysis


def extract_keyword(subject: str) -> str:
    m = re.search(r"Mention of '(.+?)' on Reddit", subject)
    return m.group(1) if m else ""

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

    new_threads, seen_to_commit = fetch_new_threads()
    print(f"Found {len(new_threads)} new thread(s)")
    seen_urls: set[str] = set()
    links: list[tuple[str, str]] = []
    for t in new_threads:
        keyword = extract_keyword(t["subject"])
        for link in t["reddit_links"]:
            if link not in seen_urls:
                seen_urls.add(link)
                links.append((link, keyword))
    print(f"Analyzing {len(links)} post(s)")
    if links:
        results = await run_analysis(links, on_flag=send_post)
        flagged = sum(1 for _, a in results if a.strip().upper().startswith("FLAG"))
        print(f"{flagged}/{len(results)} flagged")
    save_seen(seen_to_commit)
    print("Done")


if __name__ == "__main__":
    asyncio.run(run())
