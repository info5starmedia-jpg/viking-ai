import os
import pytest

pytestmark = pytest.mark.integration

def test_gemini_smoke():
    import google.generativeai as genai

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    assert api_key, "Missing GEMINI_API_KEY (or GOOGLE_API_KEY)"

    model_name = os.getenv("GEMINI_MODEL_FAST", "gemini-2.5-flash")
    genai.configure(api_key=api_key)

    model = genai.GenerativeModel(model_name)
    resp = model.generate_content("Say hello from Gemini.")
    text = getattr(resp, "text", "") or ""
    assert len(text) > 0
