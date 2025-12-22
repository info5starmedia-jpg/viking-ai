"""
codex.py – Viking AI 'brain' helpers for intel, news, and chat.

These functions are all **async** and are designed to be awaited from bot.py.

They internally call orchestrator_agent.orchestrate_query (which is synchronous)
in a background thread so they don't block the Discord event loop.
"""

from __future__ import annotations

import asyncio
from typing import Optional

try:
    from orchestrator_agent import orchestrate_query
except Exception:
    orchestrate_query = None  # type: ignore


# ---------------------------------------------------------------------------
# Small helper to safely call orchestrator_query from async code
# ---------------------------------------------------------------------------


async def _run_llm(prompt: str, mode: str = "auto") -> str:
    """
    Run the main orchestrator in a background thread.

    This works whether orchestrate_query is synchronous (current behavior)
    or later becomes synchronous-but-heavy. If orchestrate_query is missing,
    we just return the prompt so callers can still show *something*.
    """
    if orchestrate_query is None:
        return (
            "LLM orchestrator is not configured, so I cannot synthesize a full answer.\n\n"
            f"Here is the raw prompt that would have been sent:\n\n{prompt}"
        )

    loop = asyncio.get_running_loop()

    def _call() -> str:
        # Most versions of orchestrate_query(prompt, mode="auto") are sync.
        try:
            return orchestrate_query(prompt, mode=mode)  # type: ignore[misc]
        except TypeError:
            # Older versions may not accept mode=
            return orchestrate_query(prompt)  # type: ignore[call-arg]

    return await loop.run_in_executor(None, _call)


# ---------------------------------------------------------------------------
# /intel – build_tour_intel helper
# ---------------------------------------------------------------------------


async def build_tour_intel(
    artist: str,
    region: str,
    tm_block: str,
    news_block: str,
    streaming_block: str,
    demand_block: str,
) -> str:
    """
    Build a rich tour intel report from the pre-computed blocks.

    This is exactly what bot.py expects:
      await build_tour_intel(
          artist=artist,
          region=region,
          tm_block=tm_block,
          news_block=news_block,
          streaming_block=streaming_block,
          demand_block=demand_block,
      )
    """
    artist = (artist or "").strip()
    region = (region or "").strip()

    prompt = f"""
You are Viking AI, a touring and ticketing strategist.

I will give you pre-computed blocks of information about an artist's current
touring situation. Your job is to synthesize a clear, practical report for
talent buyers, promoters, or marketing teams.

Artist: {artist or "Unknown"}
Region focus: {region or "Global"}

1) Ticketmaster snapshot:
{tm_block or "No Ticketmaster data was available."}

2) News & buzz (tour / ticket related):
{news_block or "No strong tour/ticket news hits were found."}

3) Streaming & digital demand:
{streaming_block or "No streaming metrics were available."}

4) Demand rating / interpretation:
{demand_block or "No explicit demand rating was computed."}

Write a markdown report with:

- A short headline and 1–2 sentence executive summary.
- A section **Ticketmaster / On-Sale Picture** with bullets about key cities, venue levels, and ticket status.
- A section **News & Buzz** summarizing the current media / announcement situation.
- A section **Streaming & Fan Demand** interpreting streaming metrics and fan heat.
- A concise **Opportunity / Risk** section with 3–5 bullets on what a promoter should know.

Be concrete and use the blocks directly. Do *not* invent specific dates or venues
that are not already implied. Keep the whole report under ~500 words.
""".strip()

    return await _run_llm(prompt)


# ---------------------------------------------------------------------------
# /news_now – build_tour_news_brief helper
# ---------------------------------------------------------------------------


async def build_tour_news_brief(
    query: str,
    region: str,
    news_block: str,
) -> str:
    """
    Summarize the news_block for /news_now into a short markdown brief.

    bot.py calls:
        brief = await build_tour_news_brief(query, region, news_block)
    """
    query = (query or "").strip()
    region = (region or "").strip()

    prompt = f"""
You are Viking AI, a live touring / ticketing analyst.

I will give you a markdown-style bullet list of recent news headlines and snippets
for a tour / tickets search.

Query: {query or "Unknown"}
Region focus: {region or "Global"}

News items (markdown list):
{news_block or "No news items were found."}

Write a short markdown brief with:

- A title line "Tour / Ticket News – <Artist or Query> (<Region>)".
- 2–4 bullet points summarizing the *tour and ticketing* situation (on-sales,
  new dates, added shows, cancellations, presales, etc.).
- If relevant, highlight urgency (fast sell-outs, high demand).
- Finish with a single line starting with "Quick take:" and a one-sentence summary.

Keep it under ~250 words. Focus only on touring/tickets, not gossip.
""".strip()

    return await _run_llm(prompt)


# ---------------------------------------------------------------------------
# /chat – chat_with_viking helper
# ---------------------------------------------------------------------------


async def chat_with_viking(message: str, context_hint: str = "") -> str:
    """
    General chat brain for /chat.

    bot.py calls:
        reply = await chat_with_viking(message, context_hint=context_hint)
    """
    message = (message or "").strip()

    system_context = """
You are Viking AI, a Discord assistant for tour promoters, agents, and
marketing teams. You specialize in:

- Ticketmaster / on-sale strategy
- Tour routing and demand evaluation
- SEO and digital growth for artists
- Streaming / social metrics and fan demand

You answer concisely, with practical steps and bullets.
If the user asks about their existing Viking AI commands, you may reference
things like /tm_tomorrow, /intel, /news_now, /seo_audit, etc., but DO NOT
pretend to actually run them here – you're just explaining concepts.
""".strip()

    full_prompt = f"""
{system_context}

Context (optional, may be empty):
{context_hint or "(no special context)"}

User message:
{message}

Now respond as Viking AI. Be helpful, concise, and focused on tours, tickets,
SEO, or strategy as appropriate.
""".strip()

    return await _run_llm(full_prompt)
