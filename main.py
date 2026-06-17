from dotenv import load_dotenv
load_dotenv()

import asyncio
import os
import re
from playwright.async_api import async_playwright
from f5bot import login, reenable_keywords
from notify import send_notification, send_post
from gmail import fetch_new_threads
from reddit import load_subreddits, fetch_all_subreddits
from analyze import run_analysis
import db


def extract_keyword(subject: str) -> str:
    m = re.search(r"Mention of '(.+?)' on Reddit", subject)
    return m.group(1) if m else ""

async def run() -> None:
    await db.init_pool()
    try:
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

        new_threads = await fetch_new_threads()
        print(f"Found {len(new_threads)} new thread(s)")
        seen_urls: set[str] = set()
        f5bot_links: list[tuple[str, str]] = []
        for t in new_threads:
            keyword = extract_keyword(t["subject"])
            for link in t["reddit_links"]:
                if link not in seen_urls and not await db.is_seen_post(link):
                    seen_urls.add(link)
                    f5bot_links.append((link, keyword))

        subreddits = load_subreddits()
        subreddit_links: list[tuple[str, str]] = []
        if subreddits:
            print(f"Fetching posts from {len(subreddits)} subreddit(s)")
            subreddit_fetched = await fetch_all_subreddits(subreddits)
            for url, subreddit in subreddit_fetched:
                if url not in seen_urls and not await db.is_seen_post(url):
                    seen_urls.add(url)
                    subreddit_links.append((url, subreddit))
            print(f"  {len(subreddit_links)} new subreddit post(s) added")

        print(f"Analyzing {len(f5bot_links)} f5bot + {len(subreddit_links)} subreddit post(s)")

        if f5bot_links:
            f5bot_results = await run_analysis(f5bot_links, on_flag=send_post)
            f5bot_flagged = sum(1 for _, a in f5bot_results if a.strip().upper().startswith("FLAG"))
            print(f"f5bot: {f5bot_flagged}/{len(f5bot_results)} flagged, all sent")

        if subreddit_links:
            sub_results = await run_analysis(subreddit_links, on_flag=send_post)
            sub_flagged = sum(1 for _, a in sub_results if a.strip().upper().startswith("FLAG"))
            print(f"subreddit: {sub_flagged}/{len(sub_results)} flagged, all sent")

        for url in seen_urls:
            await db.mark_seen_post(url)
        print("Done")
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(run())
