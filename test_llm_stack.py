import os, openai
from openai import OpenAI
import requests
from dotenv import load_dotenv

load_dotenv()

print("=== Checking OpenAI ===")
try:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":"test"}],
        max_tokens=5,
    )
    print("PASS: OpenAI works")
except Exception as e:
    print("FAIL:", e)

print("\n=== Checking Gemini ===")
try:
    import google.generativeai as genai
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model = genai.GenerativeModel("gemini-1.5-flash")
    out = model.generate_content("test")
    print("PASS: Gemini works")
except Exception as e:
    print("FAIL:", e)

print("\n=== Checking OpenRouter ===")
try:
    headers = {
        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
        "Content-Type": "application/json"
    }
    data = {
        "model": os.getenv("OPENROUTER_MODEL"),
        "messages":[{"role":"user","content":"test"}]
    }
    r = requests.post("https://openrouter.ai/api/v1/chat/completions",
                      json=data, headers=headers)
    print("OpenRouter status:", r.status_code, r.text[:200])
except Exception as e:
    print("FAIL:", e)
