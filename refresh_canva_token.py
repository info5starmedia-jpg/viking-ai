import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CANVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("CANVA_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("CANVA_REFRESH_TOKEN")

def refresh_canva_token():
    if not CLIENT_ID or not CLIENT_SECRET or not REFRESH_TOKEN:
        print("❌ Missing Canva credentials in .env")
        return

    print("🔁 Refreshing Canva access token...")

    url = "https://www.canva.com/api/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN
    }

    try:
        response = requests.post(url, data=payload)
        data = response.json()

        if "access_token" in data:
            new_access_token = data["access_token"]
            print("✅ Canva access token refreshed successfully!")

            # Update .env file automatically
            lines = []
            with open(".env", "r") as file:
                for line in file:
                    if line.startswith("CANVA_ACCESS_TOKEN="):
                        lines.append(f"CANVA_ACCESS_TOKEN={new_access_token}\n")
                    else:
                        lines.append(line)

            with open(".env", "w") as file:
                file.writelines(lines)

            print("💾 .env updated with new token.")
        else:
            print(f"⚠️ Token refresh failed: {data}")
    except Exception as e:
        print(f"❌ Error refreshing Canva token: {e}")


if __name__ == "__main__":
    # Run once every 24 hours
    while True:
        refresh_canva_token()
        print("🕒 Sleeping for 24 hours...")
        time.sleep(24 * 60 * 60)
