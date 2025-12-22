import os
import logging

logger = logging.getLogger("orchestrator_v2")

# ---------- OpenAI ----------
from openai import OpenAI
openai_client = None
if os.getenv("OPENAI_API_KEY"):
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ---------- Gemini ----------
gemini_available = False
try:
    import google.generativeai as genai
    if os.getenv("GEMINI_API_KEY"):
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        gemini_available = True
except Exception as e:
    logger.warning(f"Gemini init failed: {e}")


def run_llm_analysis(prompt: str) -> str:
    """
    GPT-4.1-mini → Gemini → deterministic fallback
    """

    # ---- 1️⃣ OpenAI (primary) ----
    if openai_client:
        try:
            resp = openai_client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": "You are a concise touring intelligence analyst."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=180,
                temperature=0.4,
            )
            return resp.choices[0].message.content.strip()

        except Exception as e:
            msg = str(e)
            if "429" in msg or "quota" in msg.lower():
                logger.warning("OpenAI quota hit — falling back to Gemini.")
            else:
                logger.warning(f"OpenAI error — falling back: {e}")

    # ---- 2️⃣ Gemini (secondary) ----
    if gemini_available:
        try:
            model = genai.GenerativeModel("gemini-1.5-pro")
            resp = model.generate_content(prompt)
            return resp.text.strip()
        except Exception as e:
            logger.warning(f"Gemini error — fallback used: {e}")

    # ---- 3️⃣ Deterministic fallback ----
    return (
        "VikingAI Analysis: Demand signals are solid, but LLM narrative "
        "is temporarily unavailable. Metrics above remain accurate."
    )

# --- Compatibility wrapper (older modules expect this name) ---
def run_orchestrator(prompt: str) -> str:
    return run_llm_analysis(prompt)


# -------------------------------------------------------------------
# Backward-compat: older modules expect run_orchestrator()
# -------------------------------------------------------------------
def run_orchestrator(prompt: str) -> str:
    return run_llm_analysis(prompt)
