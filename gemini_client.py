import google.generativeai as genai
import os

# Load API key
GEMINI_KEY = os.getenv("GOOGLE_API_KEY")

if not GEMINI_KEY:
    raise ValueError("❌ Missing GOOGLE_API_KEY in .env")

genai.configure(api_key=GEMINI_KEY)

# Models
GEMINI_FAST = os.getenv("GEMINI_MODEL_FAST", "gemini-2.5-flash")
GEMINI_DEEP = os.getenv("GEMINI_MODEL_DEEP", "gemini-2.5-pro")


def gemini_fast(prompt: str) -> str:
    """Fast model for summaries, SEO keywords, backlinks, quick analysis."""
    try:
        model = genai.GenerativeModel(GEMINI_FAST)
        resp = model.generate_content(prompt)
        return resp.text
    except Exception as e:
        return f"❌ Gemini Fast Error: {e}"


def gemini_deep(prompt: str) -> str:
    """Deep model for long research, tour intel, and complex queries."""
    try:
        model = genai.GenerativeModel(GEMINI_DEEP)
        resp = model.generate_content(prompt)
        return resp.text
    except Exception as e:
        return f"❌ Gemini Deep Error: {e}"
