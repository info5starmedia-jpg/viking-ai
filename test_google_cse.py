def main():
    import os, requests
    from dotenv import load_dotenv
    load_dotenv()
    API_KEY = os.getenv("GOOGLE_CUSTOM_SEARCH_API_KEY")
    CX = os.getenv("GOOGLE_CUSTOM_SEARCH_ENGINE_ID")
    params = {"key": API_KEY, "cx": CX, "q": "AI trends 2025"}
    r = requests.get("https://www.googleapis.com/customsearch/v1", params=params)
    if r.ok:
        print("✅ Google CSE API working!")
    else:
        print(f"❌ Google CSE error: {r.text}")
