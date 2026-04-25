from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from dataclasses import dataclass

import anthropic
import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types
from openai import OpenAI
from tqdm import tqdm
from tqdm.asyncio import tqdm as atqdm

load_dotenv()

_PRODUCT_FILE = "PRODUCT.md"
_STRATEGY_FILE = "STRATEGY.md"
_INSIGHTS_FILE = "INSIGHTS.md"

_MAX_PAGE_CHARS = 8_000
_MAX_FETCH_CHARS = 40_000
_RESEARCH_MODEL = "claude-opus-4-7"
_GEMINI_MODEL = "gemini-2.0-flash"
_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
_OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "ollama")
_ANALYSIS_MODEL = os.environ.get("GEMMA_MODEL", "gemma4:e2b")
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
    permalink: str
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
            print(f"[analyze] Rate limited — retrying in {wait:.1f}s (attempt {attempt + 1}/{_MAX_RETRIES})")
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
            print(f"[analyze] Using existing {_INSIGHTS_FILE} ({age_days:.1f}d old)")
            return open(_INSIGHTS_FILE, encoding="utf-8").read()
        print(f"[analyze] {_INSIGHTS_FILE} is {age_days:.1f}d old — regenerating")

    if not os.path.exists(_PRODUCT_FILE):
        raise FileNotFoundError(
            f"{_PRODUCT_FILE} not found. Create it with your product description and competitor URLs."
        )
    product_text = open(_PRODUCT_FILE, encoding="utf-8").read()

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
    print("[analyze] Step 1/3: Competitor summaries done")

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
    print("[analyze] Step 2/3: Comparative analysis done")

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
    print("[analyze] Step 3/3: Final synthesis done")

    with open(_INSIGHTS_FILE, "w", encoding="utf-8") as f:
        f.write(insights)
    print(f"[analyze] Product research written to {_INSIGHTS_FILE}")
    return insights


_FETCH_TOOL = {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": "Fetch the raw text content of a URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
            },
            "required": ["url"],
        },
    },
}


_FETCH_RETRY_STATUSES = {429, 500, 502, 503, 504}
_FETCH_MAX_RETRIES = 3


def _fetch_via_gemini(url: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(f"[analyze] 403 on {url} — could not recover (GEMINI_API_KEY not set)")
        return "(GEMINI_API_KEY not set, cannot fall back to Gemini)"
    print(f"[analyze] 403 on {url} — attempting Gemini recovery")
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=_GEMINI_MODEL,
            contents=(
                f"Retrieve and return the full content of this Reddit post, including "
                f"the title, score, subreddit, post body, and top comments: {url}"
            ),
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            ),
        )
        if response.text:
            print(f"[analyze] 403 on {url} — recovered via Gemini")
            return response.text[:_MAX_FETCH_CHARS]
        print(f"[analyze] 403 on {url} — could not recover (Gemini returned no content)")
        return "(Gemini returned no content)"
    except Exception as e:
        print(f"[analyze] 403 on {url} — could not recover (Gemini error: {e})")
        return f"(Gemini fallback failed: {e})"


def _do_fetch(url: str) -> str:
    for attempt in range(_FETCH_MAX_RETRIES):
        try:
            resp = httpx.get(url, timeout=15.0, follow_redirects=True,
                             headers={"User-Agent": _BROWSER_UA})
            resp.raise_for_status()
            return resp.text[:_MAX_FETCH_CHARS]
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 403:
                return _fetch_via_gemini(url)
            if status not in _FETCH_RETRY_STATUSES or attempt == _FETCH_MAX_RETRIES - 1:
                print(f"[analyze] HTTP {status} fetching {url}")
                return f"(HTTP {status} error fetching {url})"
            wait = float(e.response.headers.get("retry-after", 2 ** attempt)) + random.uniform(0, 1)
            print(f"[analyze] HTTP {status} fetching {url} — retrying in {wait:.1f}s (attempt {attempt + 1}/{_FETCH_MAX_RETRIES})")
            time.sleep(wait)
        except httpx.RequestError as e:
            if attempt == _FETCH_MAX_RETRIES - 1:
                print(f"[analyze] Network error fetching {url}: {e}")
                return f"(network error fetching {url}: {e})"
            wait = 2 ** attempt + random.uniform(0, 1)
            print(f"[analyze] Network error fetching {url}: {e} — retrying in {wait:.1f}s (attempt {attempt + 1}/{_FETCH_MAX_RETRIES})")
            time.sleep(wait)


def _analyze_link_sync(url: str, keyword: str, insights: str, strategy: str) -> tuple[AnalyzedPost, str]:
    client = OpenAI(base_url=_OLLAMA_BASE_URL, api_key=_OLLAMA_API_KEY)

    messages: list[dict] = [
        {
            "role": "system",
            "content": f"## Advertising Strategy\n\n{strategy}\n\n## Product Insights\n\n{insights}",
        },
        {
            "role": "user",
            "content": (
                f"Reddit post URL: {url}\n"
                f"Keyword that matched: {keyword}\n\n"
                f"Fetch `{url.split('?')[0].rstrip('/')}.json?limit=500`, extract the post title, subreddit, "
                "and score from the JSON response, then decide whether to FLAG or SKIP based on "
                "the strategy and product insights above.\n\n"
                "Respond in exactly this format:\n"
                "TITLE: <post title>\n"
                "SUBREDDIT: <subreddit name>\n"
                "SCORE: <integer>\n\n"
                "Then either:\n"
                "FLAG followed by:\n"
                "  HOOK: <specific comment/question/section that is the entry point>\n"
                "  WHY: <2-3 sentences on why this is a good opportunity>\n"
                "  APPROACH: <3-5 sentences on recommended angle, tone, and specifics to reference>\n"
                "  Do not draft the comment itself.\n"
                "Or:\n"
                "SKIP <one sentence reason>\n\n"
                "Start your response with TITLE: on the first line."
            ),
        },
    ]

    for _ in range(5):
        response = client.chat.completions.create(
            model=_ANALYSIS_MODEL,
            max_tokens=1200,
            tools=[_FETCH_TOOL],
            messages=messages,
        )
        msg = response.choices[0].message
        if msg.tool_calls:
            messages.append(msg.model_dump(exclude_none=True))
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": _do_fetch(args["url"]),
                })
        else:
            break
    else:
        print(f"[analyze] Warning: tool-use loop hit max iterations for {url}")

    raw = msg.content or ""

    title_m = re.search(r"^TITLE:\s*(.+)", raw, re.MULTILINE)
    sub_m = re.search(r"^SUBREDDIT:\s*(.+)", raw, re.MULTILINE)
    score_m = re.search(r"^SCORE:\s*(\d+)", raw, re.MULTILINE)
    analysis_m = re.search(r"(FLAG|SKIP).*", raw, re.DOTALL)

    post = AnalyzedPost(
        url=url,
        keyword=keyword,
        title=title_m.group(1).strip() if title_m else "(unknown)",
        subreddit=sub_m.group(1).strip() if sub_m else "(unknown)",
        permalink=url,
        score=int(score_m.group(1)) if score_m else 0,
    )
    return post, analysis_m.group(0).strip() if analysis_m else raw


async def run_analysis(links: list[tuple[str, str]]) -> list[tuple[AnalyzedPost, str]]:
    if not os.path.exists(_STRATEGY_FILE):
        raise FileNotFoundError(
            f"{_STRATEGY_FILE} not found. Create it with your advertising strategy."
        )
    strategy = open(_STRATEGY_FILE, encoding="utf-8").read()

    insights = research_product()

    sem = asyncio.Semaphore(_ANALYSIS_CONCURRENCY)

    async def _bounded(url: str, keyword: str) -> tuple[AnalyzedPost, str]:
        async with sem:
            return await asyncio.to_thread(_analyze_link_sync, url, keyword, insights, strategy)

    results = await atqdm.gather(
        *[_bounded(url, kw) for url, kw in links],
        desc="Analyzing posts",
        unit="post",
    )
    results = list(results)

    total = len(results)
    fetched = sum(1 for post, _ in results if post.title != "(unknown)")
    flagged = sum(1 for _, analysis in results if analysis.startswith("FLAG"))
    print(f"[analyze] Done: {total} posts | {fetched}/{total} fetched | {flagged} flagged | {total - flagged} skipped")

    return results
