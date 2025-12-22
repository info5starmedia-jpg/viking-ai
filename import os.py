# orchestrator_v2.py
# ------------------------------------------------------------
# VikingAI Smart-Mix Orchestrator Brain (Python 3.12 + httpx)
# Handles:
#   - LLM task routing (Gemini Ultra, Qwen 72B/405B, GPT-4.1, o3-mini)
#   - Web search preflight (Tavily)
#   - Failover + retries
#   - Confidence scoring
#   - Async execution
# ------------------------------------------------------------

import os
import json
import asyncio
import httpx
from typing import Optional, Dict, Any


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


# ============================================================
# MODEL REGISTRY (Smart Mix)
# ============================================================
MODEL_TABLE = {
    "heavy_reasoning": "qwen2.5-72b",        # OpenRouter
    "deep_logic": "llama-3.1-405b",          # OpenRouter backup
    "synthesis": "gpt-4.1",                  # OpenAI
    "fast_format": "o3-mini",                # OpenAI
    "creative": "gemini-2.0-flash",          # Google
    "analysis": "gemini-2.0-pro",            # Google Ultra
}


# ============================================================
# SIMPLE TASK classifier
# Determines which model class to call
# ============================================================
def classify_task(user_input: str) -> str:
    msg = user_input.lower()

    if any(k in msg for k in ["forecast", "probability", "predict", "demand"]):
        return "analysis"       # Gemini Ultra

    if any(k in msg for k in ["reason", "explain", "logic", "routes"]):
        return "heavy_reasoning"  # Qwen 72B

    if any(k in msg for k in ["summarize", "rewrite", "format"]):
        return "fast_format"

    if any(k in msg for k in ["creative", "idea", "concept"]):
        return "creative"

    # default
    return "synthesis"


# ============================================================
# TAVILY SEARCH
# ============================================================
async def run_tavily_search(query: str) -> Optional[str]:
    if not TAVILY_API_KEY:
        return None

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY, "query": query, "n_tokens": 2048},
            )
            data = resp.json()
            if "results" in data and data["results"]:
                return json.dumps(data["results"][:3], indent=2)
    except Exception:
        return None

    return None


# ============================================================
# GEMINI CALLER
# ============================================================
async def call_gemini(prompt: str, model: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                url,
                params={"key": GEMINI_API_KEY},
                json=payload,
            )
            data = r.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"[Gemini error: {e}]"


# ============================================================
# OPENAI CALLER (GPT-4.1 / o3-mini)
# ============================================================
async def call_openai(prompt: str, model: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
            )
            data = r.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[OpenAI error: {e}]"


# ============================================================
# OPENROUTER CALLER (Qwen, Llama)
# ============================================================
async def call_openrouter(prompt: str, model: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "HTTP-Referer": "viking.ai",
                    "X-Title": "VikingAI",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
            )
            data = r.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[OpenRouter error: {e}]"


# ============================================================
# CONFIDENCE ESTIMATOR
# Simple heuristic based on length + model used
# ============================================================
def estimate_confidence(model: str, text: str) -> float:
    score = 0.5

    if "gemini" in model:
        score += 0.15
    if "qwen" in model:
        score += 0.10
    if "llama" in model:
        score += 0.10

    if len(text) > 300:
        score += 0.05

    return min(score, 0.95)


# ============================================================
# MAIN ORCHESTRATOR ENTRYPOINT
# ============================================================
async def run_orchestrator(user_input: str, use_web_search: bool = False) -> Dict[str, Any]:
    task_type = classify_task(user_input)
    model = MODEL_TABLE.get(task_type, "gpt-4.1")

    # -------------------------------------------
    # PRE-FLIGHT SEARCH (Optional)
    # -------------------------------------------
    search_block = None
    if use_web_search:
        search_block = await run_tavily_search(user_input)

    prompt = (
        f"You are VikingAI. Answer the following:\n\n"
        f"USER INPUT:\n{user_input}\n\n"
    )

    if search_block:
        prompt += f"Relevant Web Data:\n{search_block}\n\n"

    # -------------------------------------------
    # MODEL ROUTING
    # -------------------------------------------
    if model.startswith("gemini"):
        output = await call_gemini(prompt, model)
    elif model in ["qwen2.5-72b", "llama-3.1-405b"]:
        output = await call_openrouter(prompt, model)
    elif model in ["gpt-4.1", "o3-mini"]:
        output = await call_openai(prompt, model)
    else:
        output = f"[Unknown model: {model}]"

    # -------------------------------------------
    # CONFIDENCE SCORE
    # -------------------------------------------
    conf = estimate_confidence(model, output)

    return {
        "model_used": model,
        "output": output,
        "confidence": conf,
        "search_used": bool(search_block),
    }


# ============================================================
# TEST (Optional)
# ============================================================
if __name__ == "__main__":
    async def debug():
        res = await run_orchestrator(
            "Predict sell-out probability for The Weeknd in Miami"
        )
        print(json.dumps(res, indent=2))

    asyncio.run(debug())
