from __future__ import annotations

import asyncio
import os
import re
import time

import anthropic
import httpx

from reddit import RedditPost

_PRODUCT_FILE = "PRODUCT.md"
_STRATEGY_FILE = "STRATEGY.md"
_INSIGHTS_FILE = "INSIGHTS.md"

_MAX_PAGE_CHARS = 8_000
_RESEARCH_MODEL = "claude-opus-4-7"
_ANALYSIS_MODEL = "claude-sonnet-4-6"
_INSIGHTS_MAX_AGE_DAYS = int(os.environ.get("INSIGHTS_MAX_AGE_DAYS", "7"))


def _fetch_url_text(url: str) -> str:
    try:
        resp = httpx.get(url, timeout=10.0, follow_redirects=True,
                         headers={"User-Agent": "f5bot-agent/1.0"})
        text = resp.text
        # Strip HTML tags roughly
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
    for url in urls:
        url = url.rstrip(".,)")
        content = _fetch_url_text(url)
        competitor_blocks.append({"url": url, "content": content})

    client = anthropic.Anthropic()

    system_prompt = "You are a product research analyst helping craft organic Reddit advertising strategy."

    # Build base content blocks with competitor pages cached
    base_content: list[dict] = [
        {"type": "text", "text": f"## Product Description\n\n{product_text}\n\n## Competitor Pages\n\n"},
    ]
    for i, block in enumerate(competitor_blocks):
        entry: dict = {"type": "text", "text": f"### {block['url']}\n{block['content']}\n\n"}
        if i == len(competitor_blocks) - 1:
            entry["cache_control"] = {"type": "ephemeral"}
        base_content.append(entry)

    # Step 1: Summarize each competitor individually
    step1_content = base_content + [
        {"type": "text", "text": "Summarize each competitor site above in 3-5 bullet points: what they offer, their positioning, and their apparent strengths."}
    ]
    step1_resp = client.messages.create(
        model=_RESEARCH_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": step1_content}],
    )
    competitor_summaries = step1_resp.content[0].text
    print("[analyze] Step 1/3: Competitor summaries done")

    # Step 2: Comparative analysis — cache the summaries from step 1
    step2_resp = client.messages.create(
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

    # Step 3: Final synthesis — ideal customer, talking points, Reddit positioning
    step3_resp = client.messages.create(
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


def _analyze_post_sync(post: RedditPost, insights: str, strategy: str) -> str:
    client = anthropic.Anthropic()

    response = client.messages.create(
        model=_ANALYSIS_MODEL,
        max_tokens=800,
        system=[
            {
                "type": "text",
                "text": f"## Advertising Strategy\n\n{strategy}",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"## Product Insights\n\n{insights}",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"## Reddit Post\n\n{str(post)}\n\n"
                            "Based on the strategy and product insights above, should we engage with this post?\n"
                            "- If YES: flag the post using this structure:\n"
                            "  HOOK: Identify the specific comment, question, or section that is the entry point.\n"
                            "  WHY: Explain in 2-3 sentences why this is a good opportunity — what pain point or gap "
                            "is being expressed, and why our product is a natural fit here.\n"
                            "  APPROACH: Outline in 3-5 sentences the recommended angle — what to lead with, "
                            "how to frame the product mention naturally, what tone to use, and any specific "
                            "detail from the thread to reference so the response feels genuine.\n"
                            "  Do not draft the comment itself.\n"
                            "- If NO: explain why in one sentence.\n"
                            "Start your response with either FLAG or SKIP."
                        ),
                    },
                ],
            }
        ],
    )
    return response.content[0].text


async def run_analysis(posts: list[RedditPost]) -> list[tuple[RedditPost, str]]:
    if not os.path.exists(_STRATEGY_FILE):
        raise FileNotFoundError(
            f"{_STRATEGY_FILE} not found. Create it with your advertising strategy."
        )
    strategy = open(_STRATEGY_FILE, encoding="utf-8").read()

    insights = research_product()

    tasks = [
        asyncio.to_thread(_analyze_post_sync, post, insights, strategy)
        for post in posts
    ]
    analyses = await asyncio.gather(*tasks)
    return list(zip(posts, analyses))
