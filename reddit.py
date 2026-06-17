import os
import re
import httpx

REDDIT_API_BASE = "https://www.reddit.com"


_SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{1,21}$")


def load_subreddits(path: str = "SUBREDDITS.md") -> list[str]:
    try:
        with open(path) as f:
            return [
                line.strip()
                for line in f
                if _SUBREDDIT_RE.match(line.strip())
            ]
    except FileNotFoundError:
        return []


async def fetch_subreddit_posts(subreddit: str, limit: int = 100) -> list[str]:
    url = f"{REDDIT_API_BASE}/r/{subreddit}/new.json?limit={limit}"
    headers = {"User-Agent": "f5bot-agent/1.0 (automated monitoring)"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, follow_redirects=True, timeout=10)
            resp.raise_for_status()
            children = resp.json()["data"]["children"]
            posts = [REDDIT_API_BASE + child["data"]["permalink"] for child in children]
            print(f"  r/{subreddit}: fetched {len(posts)} post(s)")
            return posts
    except Exception as e:
        print(f"  r/{subreddit}: failed to fetch ({e})")
        return []


async def fetch_all_subreddits(subreddits: list[str]) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for subreddit in subreddits:
        posts = await fetch_subreddit_posts(subreddit)
        for url in posts:
            results.append((url, subreddit))
    return results
