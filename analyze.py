from __future__ import annotations

import asyncio
import os
import random
import re
import time
from dataclasses import dataclass

import anthropic
import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from tqdm import tqdm
from tqdm.asyncio import tqdm as atqdm

_PRODUCT_FILE = "PRODUCT.md"
_STRATEGY_FILE = "STRATEGY.md"
_INSIGHTS_FILE = "INSIGHTS.md"

_MAX_PAGE_CHARS = 8_000
_RESEARCH_MODEL = "claude-opus-4-7"
_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
_INSIGHTS_MAX_AGE_DAYS = int(os.environ.get("INSIGHTS_MAX_AGE_DAYS", "7"))
_ANALYSIS_CONCURRENCY = int(os.environ.get("ANALYSIS_CONCURRENCY", "3"))
_MAX_RETRIES = 4

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)



@dataclass
class AnalyzedPost:
    url: str
    keyword: str
    title: str
    subreddit: str
    score: int


def _create_with_retry(client: anthropic.Anthropic, **kwargs) -> anthropic.types.Message:
    for attempt in range(_MAX_RETRIES):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            headers = getattr(getattr(exc, "response", None), "headers", {})
            retry_after = float(headers.get("retry-after", 2 ** attempt))
            wait = retry_after + random.uniform(0, 1)
            time.sleep(wait)


def _fetch_url_text(url: str) -> str:
    try:
        resp = httpx.get(url, timeout=10.0, follow_redirects=True,
                         headers={"User-Agent": "f5bot-agent/1.0"})
        text = resp.text
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:_MAX_PAGE_CHARS]
    except Exception as e:
        return f"(failed to fetch {url}: {e})"



def research_product() -> str:
    if os.path.exists(_INSIGHTS_FILE):
        age_days = (time.time() - os.path.getmtime(_INSIGHTS_FILE)) / 86400
        if age_days < _INSIGHTS_MAX_AGE_DAYS:
            with open(_INSIGHTS_FILE, encoding="utf-8") as f:
                return f.read()

    if not os.path.exists(_PRODUCT_FILE):
        raise FileNotFoundError(
            f"{_PRODUCT_FILE} not found. Create it with your product description and competitor URLs."
        )
    with open(_PRODUCT_FILE, encoding="utf-8") as f:
        product_text = f.read()

    urls = re.findall(r"https?://\S+", product_text)
    competitor_blocks = []
    for url in tqdm(urls, desc="Fetching competitors", unit="url"):
        url = url.rstrip(".,)")
        content = _fetch_url_text(url)
        competitor_blocks.append({"url": url, "content": content})

    client = anthropic.Anthropic()

    system_prompt = "You are a product research analyst helping craft organic Reddit advertising strategy."

    base_content: list[dict] = [
        {"type": "text", "text": f"## Product Description\n\n{product_text}\n\n## Competitor Pages\n\n"},
    ]
    for i, block in enumerate(competitor_blocks):
        entry: dict = {"type": "text", "text": f"### {block['url']}\n{block['content']}\n\n"}
        if i == len(competitor_blocks) - 1:
            entry["cache_control"] = {"type": "ephemeral"}
        base_content.append(entry)

    step1_content = base_content + [
        {"type": "text", "text": "Summarize each competitor site above in 3-5 bullet points: what they offer, their positioning, and their apparent strengths."}
    ]
    step1_resp = _create_with_retry(client,
        model=_RESEARCH_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": step1_content}],
    )
    competitor_summaries = step1_resp.content[0].text

    step2_resp = _create_with_retry(client,
        model=_RESEARCH_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[
            {"role": "user", "content": step1_content},
            {"role": "assistant", "content": competitor_summaries},
            {"role": "user", "content": [
                {
                    "type": "text",
                    "text": competitor_summaries,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": (
                        "Now compare those competitors against our product. For each competitor, identify:\n"
                        "- Where our product is stronger\n"
                        "- Where our product is weaker or missing features\n"
                        "- Gaps in the market neither fully addresses\n"
                        "Keep it to 3-5 bullets per competitor."
                    ),
                },
            ]},
        ],
    )
    comparative_analysis = step2_resp.content[0].text

    step3_resp = _create_with_retry(client,
        model=_RESEARCH_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[
            {"role": "user", "content": [
                {
                    "type": "text",
                    "text": f"## Competitor Summaries\n\n{competitor_summaries}\n\n## Comparative Analysis\n\n{comparative_analysis}",
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": (
                        "Based on all of the above research, produce the final product insights:\n"
                        "1. Core product strengths and unique differentiators (3-5 bullets)\n"
                        "2. Ideal customer profile (3-5 bullets)\n"
                        "3. Key talking points for organic Reddit promotion (3-5 bullets)\n"
                        "4. Situations where our product is the obvious recommendation (3-5 bullets)"
                    ),
                },
            ]},
        ],
    )
    insights = (
        f"## Competitor Summaries\n\n{competitor_summaries}\n\n"
        f"## Comparative Analysis\n\n{comparative_analysis}\n\n"
        f"## Final Insights\n\n{step3_resp.content[0].text}"
    )
    with open(_INSIGHTS_FILE, "w", encoding="utf-8") as f:
        f.write(insights)
    return insights


def _analyze_link_sync(url: str, keyword: str, insights: str, strategy: str) -> tuple[AnalyzedPost, str]:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    config = types.GenerateContentConfig(
        system_instruction=f"## Advertising Strategy\n\n{strategy}\n\n## Product Insights\n\n{insights}",
        tools=[types.Tool(google_search=types.GoogleSearch())],
        max_output_tokens=600,
    )

    user_text = (
        f"Reddit post URL: {url}\n"
        f"Keyword that matched: {keyword}\n\n"
        f"Retrieve the Reddit post at the URL above, then decide whether to FLAG or SKIP based on "
        "the strategy and product insights above.\n\n"
        "Respond in exactly this format:\n"
        "TITLE: <post title>\n"
        "SUBREDDIT: <subreddit name>\n"
        "SCORE: <integer>\n\n"
        "Then either:\n"
        "FLAG\n"
        "WHY: <one sentence on why this is a good opportunity>\n"
        "ANGLE: <one to two sentences on the recommended approach and tone>\n"
        "Or:\n"
        "SKIP <one sentence reason>\n\n"
        "Start your response with TITLE: on the first line."
    )

    def _gemini_call():
        for attempt in range(4):
            try:
                return client.models.generate_content(
                    model=_GEMINI_MODEL, contents=user_text, config=config
                )
            except genai_errors.ServerError as exc:
                if exc.code != 503 or attempt == 3:
                    raise
                wait = (2 ** attempt) * 5 + random.uniform(0, 2)
                time.sleep(wait)

    response = _gemini_call()

    raw = response.text or "" if response else ""

    title_m = re.search(r"^TITLE:\s*(.+)", raw, re.MULTILINE)
    sub_m = re.search(r"^SUBREDDIT:\s*(.+)", raw, re.MULTILINE)
    score_m = re.search(r"^SCORE:\s*(\d+)", raw, re.MULTILINE)
    analysis_m = re.search(r"(FLAG|SKIP).*", raw, re.DOTALL)

    post = AnalyzedPost(
        url=url,
        keyword=keyword,
        title=title_m.group(1).strip() if title_m else "(unknown)",
        subreddit=sub_m.group(1).strip() if sub_m else "(unknown)",
        score=int(score_m.group(1)) if score_m else 0,
    )
    return post, analysis_m.group(0).strip() if analysis_m else raw


async def run_analysis(
    links: list[tuple[str, str]],
    on_flag: callable = None,
) -> list[tuple[AnalyzedPost, str]]:
    if not os.path.exists(_STRATEGY_FILE):
        raise FileNotFoundError(
            f"{_STRATEGY_FILE} not found. Create it with your advertising strategy."
        )
    with open(_STRATEGY_FILE, encoding="utf-8") as f:
        strategy = f.read()

    insights = research_product()

    sem = asyncio.Semaphore(_ANALYSIS_CONCURRENCY)

    async def _bounded(url: str, keyword: str) -> tuple[AnalyzedPost, str]:
        async with sem:
            try:
                post, analysis = await asyncio.to_thread(_analyze_link_sync, url, keyword, insights, strategy)
            except Exception as exc:
                print(f"  [error] analysis failed for {url}: {exc}")
                return AnalyzedPost(url=url, keyword=keyword, title="(error)", subreddit="(error)", score=0), "SKIP (analysis error)"
            return post, analysis

    results = list(await atqdm.gather(
        *[_bounded(url, kw) for url, kw in links],
        desc="Analyzing posts",
        unit="post",
    ))

    if on_flag:
        for post, analysis in results:
            if analysis.strip().upper().startswith("FLAG"):
                try:
                    on_flag(post, analysis)
                except Exception as exc:
                    print(f"  [error] notification failed for {post.url}: {exc}")

    return results
