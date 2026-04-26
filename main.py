import asyncio
import re
import subprocess
import time
import urllib.request
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from f5bot import login, reenable_keywords
from notify import send_notification, send_analyses
from gmail import fetch_new_threads
from analyze import run_analysis


def _ensure_ollama() -> None:
    try:
        urllib.request.urlopen("http://localhost:11434", timeout=2)
        return  # already running
    except Exception:
        pass
    print("Starting ollama serve...")
    subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(1)
        try:
            urllib.request.urlopen("http://localhost:11434", timeout=2)
            print("Ollama ready.")
            return
        except Exception:
            pass
    raise RuntimeError("Ollama did not start within 20 seconds")


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

    new_threads = fetch_new_threads()
    print(f"Found {len(new_threads)} new f5bot thread(s)")
    seen_urls: set[str] = set()
    links: list[tuple[str, str]] = []
    for t in new_threads:
        keyword = extract_keyword(t["subject"])
        for link in t["reddit_links"]:
            if link not in seen_urls:
                seen_urls.add(link)
                links.append((link, keyword))
    if links:
        _ensure_ollama()
        results = await run_analysis(links)
        for post, analysis in results:
            print(f"\n=== {post.title} ===")
            print(f"r/{post.subreddit} | {post.permalink}")
            print(analysis)
        send_analyses(results)


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(run())
