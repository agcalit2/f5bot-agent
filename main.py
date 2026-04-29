from dotenv import load_dotenv
load_dotenv()

import asyncio
import os
import re
from playwright.async_api import async_playwright
from f5bot import login, reenable_keywords
from notify import send_notification, send_post
from gmail import fetch_new_threads, save_seen
from reddit import load_subreddits, fetch_all_subreddits, load_seen_posts, save_seen_posts
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
    seen_posts: set[str] = load_seen_posts()
    seen_urls: set[str] = set()
    f5bot_links: list[tuple[str, str]] = []
    for t in new_threads:
        keyword = extract_keyword(t["subject"])
        for link in t["reddit_links"]:
            if link not in seen_urls and link not in seen_posts:
                seen_urls.add(link)
                f5bot_links.append((link, keyword))

    subreddits = load_subreddits()
    if subreddits:
        print(f"Fetching posts from {len(subreddits)} subreddit(s)")
        subreddit_fetched = await fetch_all_subreddits(subreddits)
        subreddit_new_urls: set[str] = set()
        subreddit_links: list[tuple[str, str]] = []
        for url, subreddit in subreddit_fetched:
            if url not in seen_urls and url not in seen_posts:
                seen_urls.add(url)
                subreddit_new_urls.add(url)
                subreddit_links.append((url, subreddit))
        print(f"  {len(subreddit_new_urls)} new subreddit post(s) added")
    else:
        subreddit_new_urls = set()
        subreddit_links = []

    print(f"Analyzing {len(f5bot_links)} f5bot + {len(subreddit_links)} subreddit post(s)")

    if f5bot_links:
        f5bot_results = await run_analysis(f5bot_links, on_flag=send_post)
        f5bot_flagged = sum(1 for _, a in f5bot_results if a.strip().upper().startswith("FLAG"))
        print(f"f5bot: {f5bot_flagged}/{len(f5bot_results)} flagged, all sent")

    if subreddit_links:
        sub_results = await run_analysis(subreddit_links, on_flag=send_post)
        sub_flagged = sum(1 for _, a in sub_results if a.strip().upper().startswith("FLAG"))
        print(f"subreddit: {sub_flagged}/{len(sub_results)} flagged, all sent")
    save_seen(seen_to_commit)
    seen_posts.update(subreddit_new_urls)
    save_seen_posts(seen_posts)
    print("Done")


if __name__ == "__main__":
    asyncio.run(run())
