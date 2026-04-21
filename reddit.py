from __future__ import annotations

import asyncio
import os
import random
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from proxy_pool import get_pool
from user_agents import random_accept_language, random_ua, random_viewport


@dataclass
class Comment:
    id: str
    parent_id: str
    author: str
    body: str
    score: int
    created_utc: float
    flagged: bool
    replies: list[Comment] = field(default_factory=list)

    def _to_str(self, indent: int) -> str:
        pad = "  " * indent
        ts = datetime.fromtimestamp(self.created_utc, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        prefix = "[FLAGGED] " if self.flagged else ""
        lines = [f"{pad}{prefix}u/{self.author} | score: {self.score} | {ts}", f"{pad}  {self.body}"]
        for reply in self.replies:
            lines.append(reply._to_str(indent + 1))
        return "\n".join(lines)

    def __str__(self) -> str:
        return self._to_str(0)


@dataclass
class RedditPost:
    url: str
    permalink: str
    title: str
    selftext: str
    author: str
    score: int
    upvote_ratio: float
    subreddit: str
    num_comments: int
    created_utc: float
    keyword: str
    comments: list[Comment] = field(default_factory=list)
    flagged_comments: list[Comment] = field(default_factory=list)

    def __str__(self) -> str:
        ts = datetime.fromtimestamp(self.created_utc, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        ratio_pct = int(self.upvote_ratio * 100)
        body_section = self.selftext.strip() if self.selftext.strip() else "(link post — no body text)"
        comment_tree = "\n\n".join(str(c) for c in self.comments) or "(no comments)"
        flagged_section = "\n".join(
            f"[{i+1}] u/{c.author} | score: {c.score}\n    {c.body}"
            for i, c in enumerate(self.flagged_comments)
        ) or "(none)"

        return (
            f"=== REDDIT POST ===\n"
            f"Keyword:      {self.keyword}\n"
            f"Subreddit:    r/{self.subreddit}\n"
            f"Title:        {self.title}\n"
            f"Author:       u/{self.author}\n"
            f"Score:        {self.score} (upvote ratio: {ratio_pct}%)\n"
            f"Comments:     {self.num_comments}\n"
            f"Posted:       {ts}\n"
            f"URL:          {self.url}\n"
            f"\n--- Original Post ---\n{body_section}\n"
            f"\n--- Comment Tree ---\n{comment_tree}\n"
            f"\n--- Flagged Comments (keyword: \"{self.keyword}\") ---\n{flagged_section}\n"
            f"=== END POST ==="
        )


def _build_comment_tree(children: list[dict], keyword: str) -> list[Comment]:
    result = []
    for entry in children:
        if entry.get("kind") != "t1":
            continue
        data = entry["data"]
        flagged = bool(keyword) and keyword.lower() in data.get("body", "").lower()
        replies_raw = data.get("replies", "")
        sub_children = replies_raw["data"]["children"] if isinstance(replies_raw, dict) else []
        result.append(Comment(
            id=data.get("id", ""),
            parent_id=data.get("parent_id", ""),
            author=data.get("author", "[deleted]"),
            body=data.get("body", ""),
            score=data.get("score", 0),
            created_utc=data.get("created_utc", 0.0),
            flagged=flagged,
            replies=_build_comment_tree(sub_children, keyword),
        ))
    return result


def _collect_flagged(comments: list[Comment]) -> list[Comment]:
    result = []
    for c in comments:
        if c.flagged:
            result.append(c)
        result.extend(_collect_flagged(c.replies))
    return result


_REDDIT_DELAY_MIN = float(os.environ.get("REDDIT_DELAY_SECONDS", "2.0"))
_REDDIT_DELAY_MAX = max(_REDDIT_DELAY_MIN + 3.0, 5.0)
_REDDIT_MAX_PROXY_ATTEMPTS = 30
_NAV_TIMEOUT_MS = 20000


def _parse_datetime_attr(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return 0.0


def _parse_old_reddit_comments(container) -> list[dict]:
    """Recursively parse old.reddit.com comment HTML into the {kind, data} shape
    that _build_comment_tree expects.
    """
    out: list[dict] = []
    if container is None:
        return out
    for thing in container.find_all("div", class_="thing", recursive=False):
        classes = thing.get("class", [])
        if "comment" not in classes:
            continue
        fullname = thing.get("data-fullname", "")
        if not fullname.startswith("t1_"):
            continue

        entry = thing.find("div", class_="entry", recursive=False)
        body_el = entry.select_one(".usertext-body .md") if entry else None
        body = body_el.get_text("\n", strip=True) if body_el else ""

        try:
            score = int(thing.get("data-score") or 0)
        except ValueError:
            score = 0

        created_utc = 0.0
        if entry:
            time_el = entry.find("time")
            if time_el:
                created_utc = _parse_datetime_attr(time_el.get("datetime"))

        child_div = thing.find("div", class_="child", recursive=False)
        nested_container = child_div.find("div", class_="sitetable", recursive=False) if child_div else None
        nested_children = _parse_old_reddit_comments(nested_container)

        replies_value = {"data": {"children": nested_children}} if nested_children else ""

        out.append({
            "kind": "t1",
            "data": {
                "id": fullname[3:],
                "parent_id": thing.get("data-parent-fullname", ""),
                "author": thing.get("data-author") or "[deleted]",
                "body": body,
                "score": score,
                "created_utc": created_utc,
                "replies": replies_value,
            },
        })
    return out


def _parse_old_reddit_html(html: str, url: str) -> tuple[dict, list[dict]] | None:
    """Parse the HTML of an old.reddit.com post page into (post_dict, comment_children).
    post_dict mirrors the fields the old .json endpoint returned so the downstream
    RedditPost construction is unchanged.
    """
    soup = BeautifulSoup(html, "html.parser")

    site_table = soup.select_one("#siteTable > div.thing.link")
    if site_table is None:
        return None

    permalink = site_table.get("data-permalink", "")
    author = site_table.get("data-author") or "[deleted]"
    subreddit = site_table.get("data-subreddit", "")

    try:
        score = int(site_table.get("data-score") or 0)
    except ValueError:
        score = 0

    title_el = site_table.select_one("a.title")
    title = title_el.get_text(strip=True) if title_el else ""

    body_el = site_table.select_one(".expando .usertext-body .md")
    selftext = body_el.get_text("\n", strip=True) if body_el else ""

    num_comments = 0
    comments_link = site_table.select_one("a.comments")
    if comments_link:
        m = re.search(r"(\d[\d,]*)\s+comment", comments_link.get_text())
        if m:
            num_comments = int(m.group(1).replace(",", ""))

    created_utc = 0.0
    time_el = site_table.select_one("time")
    if time_el:
        created_utc = _parse_datetime_attr(time_el.get("datetime"))

    post_data = {
        "permalink": permalink,
        "title": title,
        "selftext": selftext,
        "author": author,
        "score": score,
        "upvote_ratio": 0.0,  # not reliably exposed in old.reddit.com HTML
        "subreddit": subreddit,
        "num_comments": num_comments,
        "created_utc": created_utc,
    }

    comment_root = soup.select_one("div.commentarea > div.sitetable.nestedlisting")
    children = _parse_old_reddit_comments(comment_root) if comment_root else []

    return post_data, children


def _to_old_reddit_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"https://old.reddit.com{path}/?limit=500"


async def _fetch_single(browser, url: str, keyword: str, pool) -> tuple[RedditPost | None, str]:
    """Keep rotating proxies on every kind of failure until we either succeed,
    the pool is exhausted, or the per-URL safety cap is hit.

    Return (post_or_none, outcome) where outcome is one of
    success / proxy_exhausted / gave_up.
    """
    target = _to_old_reddit_url(url)

    for attempt in range(1, _REDDIT_MAX_PROXY_ATTEMPTS + 1):
        proxy = await pool.get_proxy()
        if proxy is None:
            print(f"[WARN] {url}: proxy pool exhausted after {attempt - 1} attempt(s)")
            return None, "proxy_exhausted"

        ua = random_ua()
        viewport = random_viewport()
        accept_language = random_accept_language()

        context = None
        try:
            context = await browser.new_context(
                proxy={"server": proxy},
                user_agent=ua,
                viewport=viewport,
                locale=accept_language.split(",")[0],
                extra_http_headers={"Accept-Language": accept_language},
            )
            page = await context.new_page()
            resp = await page.goto(target, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)

            if resp is None or resp.status >= 400:
                status = resp.status if resp else "no response"
                print(f"[WARN] {url} attempt {attempt}: status {status} via proxy {proxy} — rotating")
                await pool.mark_dead(proxy)
                continue

            # Small humanizing pause + one scroll so the page reads like a real visit
            await page.wait_for_timeout(random.randint(500, 1500))
            try:
                await page.mouse.wheel(0, 800)
            except Exception:
                pass

            html = await page.content()
            parsed = _parse_old_reddit_html(html, url)
            if parsed is None:
                print(f"[WARN] {url} attempt {attempt}: could not locate post node via proxy {proxy} — rotating")
                await pool.mark_dead(proxy)
                continue

            post_data, comment_children = parsed
            comments = _build_comment_tree(comment_children, keyword)
            flagged_comments = _collect_flagged(comments)
            post = RedditPost(
                url=url,
                permalink="https://reddit.com" + post_data.get("permalink", ""),
                title=post_data.get("title", ""),
                selftext=post_data.get("selftext", ""),
                author=post_data.get("author", "[deleted]"),
                score=post_data.get("score", 0),
                upvote_ratio=post_data.get("upvote_ratio", 0.0),
                subreddit=post_data.get("subreddit", ""),
                num_comments=post_data.get("num_comments", 0),
                created_utc=post_data.get("created_utc", 0.0),
                keyword=keyword,
                comments=comments,
                flagged_comments=flagged_comments,
            )
            return post, "success"
        except Exception as e:
            print(f"[WARN] {url} attempt {attempt}: {type(e).__name__}: {e} — rotating proxy")
            await pool.mark_dead(proxy)
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

    print(f"[WARN] {url}: hit safety cap of {_REDDIT_MAX_PROXY_ATTEMPTS} proxy rotations")
    return None, "gave_up"


async def fetch_posts_batch(links: list[tuple[str, str]]) -> list[RedditPost]:
    posts: list[RedditPost] = []
    stats: dict[str, int] = {
        "success": 0,
        "proxy_exhausted": 0,
        "gave_up": 0,
    }

    if not links:
        return posts

    # Shuffle so we don't hammer the same subreddit back-to-back.
    shuffled = list(links)
    random.shuffle(shuffled)

    pool = get_pool()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            for i, (url, keyword) in enumerate(shuffled):
                if i > 0:
                    await asyncio.sleep(random.uniform(_REDDIT_DELAY_MIN, _REDDIT_DELAY_MAX))

                post, outcome = await _fetch_single(browser, url, keyword, pool)
                stats[outcome] = stats.get(outcome, 0) + 1
                if post is not None:
                    posts.append(post)
                else:
                    print(f"[WARN] Could not fetch context for {url} (outcome={outcome})")
        finally:
            await browser.close()

    total = len(links)
    pct = int(stats["success"] / total * 100)
    print(
        f"[STATS] Reddit fetch: {stats['success']}/{total} succeeded ({pct}%) | "
        f"proxy_exhausted={stats['proxy_exhausted']} gave_up={stats['gave_up']}"
    )

    return posts
