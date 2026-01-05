import os
import pytest

pytestmark = pytest.mark.integration

def test_openai_smoke():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set")

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    model = os.getenv("OPENAI_MODEL_LEGACY", "gpt-4o-mini")

    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Say hello from OpenAI."}],
        )
    except Exception as e:
        s = str(e)
        if "insufficient_quota" in s or "429" in s:
            pytest.skip(f"OpenAI quota/rate limited: {e}")
        raise

    text = (r.choices[0].message.content or "").strip()
    assert text
