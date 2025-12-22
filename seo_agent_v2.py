# seo_agent_v2.py
# ------------------------------------------------------------
# SEO Intelligence Agent for Viking AI
# ------------------------------------------------------------

import httpx
from bs4 import BeautifulSoup
from typing import Dict, Any, List
from urllib.parse import urlparse


def _score_title(title: str) -> int:
    if not title:
        return 0
    length = len(title)
    if 30 <= length <= 60:
        return 100
    if 20 <= length <= 70:
        return 70
    return 40


def _score_headings(h1: List[str], h2: List[str]) -> int:
    if not h1:
        return 20
    if len(h1) > 1:
        return 50
    if len(h2) < 2:
        return 60
    return 85


def _score_wordcount(count: int) -> int:
    if count < 300:
        return 30
    if count < 800:
        return 70
    return 90


async def seo_audit(url: str) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            html = r.text
    except Exception as e:
        return {"error": f"fetch_failed: {e}"}

    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else None
    h1 = [h.text.strip() for h in soup.find_all("h1")]
    h2 = [h.text.strip() for h in soup.find_all("h2")]
    links = [a["href"] for a in soup.find_all("a", href=True)]
    text = soup.get_text(separator=" ")
    word_count = len(text.split())

    host = urlparse(url).netloc
    internal_links = [l for l in links if host in l or l.startswith("/")]
    external_links = [l for l in links if host not in l and not l.startswith("/")]

    title_score = _score_title(title or "")
    heading_score = _score_headings(h1, h2)
    content_score = _score_wordcount(word_count)

    overall = round((title_score * 0.3 + heading_score * 0.3 + content_score * 0.4), 2)

    recs: List[str] = []
    if not title:
        recs.append("Add a descriptive <title> tag with your main keyword.")
    elif title_score < 80:
        recs.append("Refine the title length (30–60 chars) and include the primary keyword near the start.")

    if not h1:
        recs.append("Add a clear H1 headline that states the main topic of the page.")
    elif len(h1) > 1:
        recs.append("Use only one H1 per page; demote extra H1s to H2 or H3.")

    if word_count < 600:
        recs.append("Increase on-page content to at least 600–800 words for better topical depth.")

    if len(internal_links) < 5:
        recs.append("Add more internal links to relevant pages to help crawl depth and topical authority.")
    if len(external_links) == 0:
        recs.append("Add a few high-quality external references to support the content (no spammy links).")

    return {
        "url": url,
        "title": title,
        "h1": h1,
        "h2": h2,
        "link_count": len(links),
        "internal_links": len(internal_links),
        "external_links": len(external_links),
        "word_count": word_count,
        "scores": {
            "title": title_score,
            "headings": heading_score,
            "content": content_score,
            "overall": overall,
        },
        "recommendations": recs,
    }
