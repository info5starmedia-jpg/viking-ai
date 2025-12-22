import os
import requests
from shutil import which
from dotenv import load_dotenv

load_dotenv()


def run_diagnostics():
    """
    Return a dict of feature -> bool for the VikingAI stack.

    LLM-related:
      - Gemini
      - OpenRouter
      - OpenAI (legacy / optional)
      - Tavily

    Other tools:
      - Canva
      - Ticketmaster
      - ElevenLabs
      - KdenliveCLI
    """
    results = {}

    # ---------- LLM STACK ----------

    # Gemini (we accept several common env var names)
    gemini_key = (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GOOGLE_GEMINI_API_KEY")
    )
    results["Gemini"] = bool(gemini_key and len(gemini_key) > 10)

    # OpenRouter (main gateway for OpenAI-style models)
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    results["OpenRouter"] = bool(openrouter_key and len(openrouter_key) > 10)

    # OpenAI (legacy / optional)
    openai_key = os.getenv("OPENAI_API_KEY")
    openai_model = os.getenv("OPENAI_MODEL")
    results["OpenAI"] = bool(openai_key and len(openai_key) > 20 and openai_model)

    # Tavily search
    tavily = os.getenv("TAVILY_API_KEY")
    results["Tavily"] = bool(tavily)

    # ---------- OTHER INTEGRATIONS ----------

    # Canva
    canva_token = os.getenv("CANVA_ACCESS_TOKEN")
    results["Canva"] = bool(canva_token and len(canva_token) > 20)

    # Ticketmaster basic check
    tm_key = os.getenv("TICKETMASTER_API_KEY")
    if tm_key:
        try:
            r = requests.get(
                "https://app.ticketmaster.com/discovery/v2/events.json",
                params={"apikey": tm_key, "keyword": "test"},
                timeout=5,
            )
            results["Ticketmaster"] = (r.status_code == 200)
        except Exception:
            results["Ticketmaster"] = False
    else:
        results["Ticketmaster"] = False

    # ElevenLabs
    eleven = os.getenv("ELEVENLABS_API_KEY")
    results["ElevenLabs"] = bool(eleven)

    # Kdenlive CLI (or melt as fallback)
    results["KdenliveCLI"] = bool(which("kdenlive_render") or which("melt"))

    return results


def format_llm_status(results: dict) -> str:
    """
    Helper for status messages, so everything uses the same wording.
    """
    gemini_ok = results.get("Gemini", False)
    or_ok = results.get("OpenRouter", False)
    tavily_ok = results.get("Tavily", False)

    parts = [
        f"Gemini: {'✅' if gemini_ok else '⚠️'}",
        f"OpenRouter: {'✅' if or_ok else '⚠️'}",
        f"Tavily: {'✅' if tavily_ok else '⚠️'}",
    ]

    overall_ok = gemini_ok or or_ok
    prefix = "✅ Ready" if overall_ok else "⚠️ Not fully configured"
    return f"{prefix} ({' / '.join(parts)})"


if __name__ == "__main__":
    # Simple CLI output if you run: python diagnostics.py
    results = run_diagnostics()
    print("🩺 VikingAI Diagnostics\n")
    for key, ok in results.items():
        mark = "✅" if ok else "⚠️"
        print(f"{mark} {key}")
    print()
    print("LLM stack:", format_llm_status(results))
