import os
import requests
from flask import Flask, request, redirect
from dotenv import load_dotenv

# === Load environment variables ===
load_dotenv()

app = Flask(__name__)

# === Load your Ticketmaster credentials ===
CLIENT_ID = os.getenv("TICKETMASTER_CLIENT_ID")
CLIENT_SECRET = os.getenv("TICKETMASTER_CLIENT_SECRET")
REDIRECT_URI = os.getenv("TICKETMASTER_REDIRECT_URI", "http://127.0.0.1:5000/oauth/callback")

# === Step 1: Redirect user to Ticketmaster login ===
@app.route("/")
def home():
    if not CLIENT_ID:
        return "❌ Missing TICKETMASTER_CLIENT_ID in .env file."
    
    auth_url = (
        "https://auth.ticketmaster.com/oauth/authorize"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=openid+profile+email+offline_access+user_read+user_write+events_read+events_write"
    )
    print(f"\n🌐 Visit this URL to authorize:\n{auth_url}\n")
    return redirect(auth_url)

# === Step 2: Handle Ticketmaster OAuth callback ===
@app.route("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")
    print(f"🪄 Received OAuth code: {code}")

    if not code:
        return "❌ No code returned — check your redirect URI configuration."

    # Exchange the code for access/refresh tokens
    token_url = "https://auth.ticketmaster.com/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }

    response = requests.post(token_url, data=data)
    print(f"🔁 Token exchange response: {response.status_code}")
    print(response.text)

    if response.status_code == 200:
        tokens = response.json()
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")

        print("\n✅ Token exchange successful!")
        print(f"Access Token: {access_token}")
        print(f"Refresh Token: {refresh_token}")

        # Save tokens to .env
        with open(".env", "a") as f:
            f.write(f"\nTICKETMASTER_ACCESS_TOKEN={access_token}")
            f.write(f"\nTICKETMASTER_REFRESH_TOKEN={refresh_token}")

        return "✅ Token exchange complete! Check your console for the tokens."
    else:
        return f"❌ Error exchanging code: {response.text}"

# === Run the local Flask server ===
if __name__ == "__main__":
    print("🚀 Visit http://127.0.0.1:5000 in your browser to authenticate with Ticketmaster.")
    app.run(port=5000)
