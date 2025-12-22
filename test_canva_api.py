def main():
    import requests, os
    from dotenv import load_dotenv
    load_dotenv()
    token = os.getenv("CANVA_ACCESS_TOKEN")
    r = requests.get("https://api.canva.com/v1/me", headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 200:
        print("✅ Canva API connection successful!")
    else:
        print(f"❌ Canva API error: {r.text}")
