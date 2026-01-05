# agents/socials_agent.py
# ------------------------------------------------------------
# Social Media Heat Agent
# Deterministic, offline-safe estimator for social buzz.
# ------------------------------------------------------------

from __future__ import annotations

import re
from typing import Any, Dict


def _estimate_heat_score(artist_name: str) -> float:
    cleaned = (artist_name or "").strip().lower()
    normalized = re.sub(r"\s+", " ", cleaned)
    letters = [ch for ch in normalized if ch.isalpha()]
    vowels = sum(ch in "aeiou" for ch in letters)
    unique_letters = len(set(letters))
    word_count = len(normalized.split()) if normalized else 0

    base = 18.0
    score = (
        base
        + (len(letters) * 2.2)
        + (vowels * 2.8)
        + (unique_letters * 1.5)
        + (word_count * 4.0)
    )
    return max(0.0, min(100.0, round(score, 1)))


def _heat_comment(score: float) -> str:
    if score >= 80:
        return "Sustained buzz across major social channels."
    if score >= 60:
        return "Strong momentum with frequent mentions."
    if score >= 40:
        return "Moderate chatter with periodic spikes."
    if score >= 20:
        return "Low buzz, limited recent chatter."
    return "Very limited social chatter detected."


async def get_socials_heat(artist_name: str) -> Dict[str, Any]:
    """
    Deterministic, offline-safe estimate of social media heat.

    Returns:
      {
        "heat_score": 0-100,
        "comment": "short explanation"
      }
    """
    score = _estimate_heat_score(artist_name)
    return {"heat_score": score, "comment": _heat_comment(score)}
