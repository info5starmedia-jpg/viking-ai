# agents/socials_agent.py
# ------------------------------------------------------------
# Social Media Heat Agent
# Very simple LLM-powered estimator for social buzz.
# ------------------------------------------------------------

import os
import httpx
from typing import Dict, Any

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")


async def get_socials_heat(artist_name: str) -> Dict[str, Any]:
    """
    Uses OpenRouter LLM to estimate a simple social media heat score.
    Returns a dict like:
      {
        "heat_score": 0-100,
        "comment": "short explanation"
      }
    """

    if not OPENROUTER_API_KEY:
        return {"error": "missing_openrouter_key"}

    prompt = (
        f"Estimate current social media buzz (Twitter/X, Instagram, TikTok) for the artist '{artist_name}'. "
        "Respond ONLY with a JSON object like:\n"
        '{ "heat_score": <number 0-100>, "comment": "<one sentence>" }'
    )

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "anthropic/claude-3.5-sonnet",
                    "messages": [
                        {"role": "system", "content": "You output ONLY valid JSON, nothing else."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                },
            )
            data = r.json()
            text = data["choices"][0]["message"]["content"]

        import json
        parsed = json.loads(text)
        # Ensure keys exist
        heat = float(parsed.get("heat_score", 0))
        comment = str(parsed.get("comment", ""))

        return {
            "heat_score": heat,
            "comment": comment,
        }

    except Exception as e:
        return {"error": str(e)}
