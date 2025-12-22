# seo_agent_v2.py
# ------------------------------------------------------------
# Simple SEO audit (sync) for Viking AI
# Provides: run_seo_audit(url) -> str
# ------------------------------------------------------------

from __future__ import annotations

import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse


def _norm_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def run_seo_audit(url: str, timeout: int = 15) -> str:
    """
    Minimal, stable SEO audit used by /seo (or equivalent command).
    Returns a human-readable report string.
    """
    url = _norm_url(url)
    if not url:
        return "❌ SEO audit error: missing URL"

    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "VikingAI/1.0"})
        status = r.status_code
        html = r.text or ""
    except Exception as e:
        return f"❌ SEO audit error: {e}"

    soup = BeautifulSoup(html, "lxml") if html else BeautifulSoup("", "html.parser")

    title = (soup.title.string.strip() if soup.title and soup.title.string else "")
    meta_desc = ""
    meta_robots = ""
    canonical = ""

    md = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if md and md.get("content"):
        meta_desc = md["content"].strip()

    mr = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    if mr and mr.get("content"):
        meta_robots = mr["content"].strip()

    link_can = soup.find("link", rel=re.compile(r"canonical", re.I))
    if link_can and link_can.get("href"):
        canonical = link_can["href"].strip()

    h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    h2_count = len(soup.find_all("h2"))
    img_count = len(soup.find_all("img"))

    parsed = urlparse(url)
    host = parsed.netloc

    issues = []
    if status >= 400:
        issues.append(f"HTTP status {status}")
    if not title:
        issues.append("Missing <title>")
    elif len(title) > 60:
        issues.append("Title too long (>60 chars)")
    if not meta_desc:
        issues.append("Missing meta description")
    elif len(meta_desc) > 160:
        issues.append("Meta description too long (>160 chars)")
    if len(h1s) == 0:
        issues.append("Missing H1")
    elif len(h1s) > 1:
        issues.append("Multiple H1s")

    report_lines = [
        f"🕵️ SEO Audit — {host}",
        f"• URL: {url}",
        f"• HTTP: {status}",
        "",
        f"🏷 Title ({len(title)} chars): {title or '—'}",
        f"📝 Meta description ({len(meta_desc)} chars): {meta_desc or '—'}",
        f"🤖 Robots: {meta_robots or '—'}",
        f"🔗 Canonical: {canonical or '—'}",
        "",
        f"🔤 H1 count: {len(h1s)}",
    ]

    if h1s:
        report_lines.append(f"• H1: {h1s[0]}")
        if len(h1s) > 1:
            report_lines.append(f"• H1 extra: {h1s[1]}")

    report_lines += [
        f"🔤 H2 count: {h2_count}",
        f"🖼 Images: {img_count}",
        "",
    ]

    if issues:
        report_lines.append("⚠️ Issues:")
        for it in issues[:10]:
            report_lines.append(f"• {it}")
    else:
        report_lines.append("✅ No major issues detected (basic checks).")

    return "\n".join(report_lines)
