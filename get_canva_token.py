import os
import base64
import hashlib
import requests
from flask import Flask, request, redirect
from dotenv import load_dotenv

# Load credentials
load_dotenv()
CLIENT_ID = os.getenv("CANVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("CANVA_CLIENT_SECRET")
REDIRECT_URI = "http://127.0.0.1:5000/oauth/callback"

# Updated Canva OAuth endpoints
AUTH_URL = "https://www.canva.com/api/oauth/authorize"
TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"

app = Flask(__name__)

# --- PKCE Helper ---
def generate_pkce_pair():
    code_verifier = base64.urlsafe_b64encode(os.urandom(40)).decode().rstrip("=")
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip("=")
    return code_verifier, code_challenge

CODE_VERIFIER, CODE_CHALLENGE = generate_pkce_pair()

@app.route("/")
def home():
    scopes = (
        "design:content:read "
        "design:content:write "
        "design:meta:read "
        "asset:read "
        "asset:write "
        "profile:read"
    )
    scope_param = scopes.replace(" ", "%20")

    auth_link = (
        f"{AUTH_URL}?"
        f"response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={scope_param}"
        f"&code_challenge_method=S256"
        f"&code_challenge={CODE_CHALLENGE}"
    )

    print("\n🌐 Visit this URL in your browser to authorize Canva access:\n", auth_link)
    return redirect(auth_link)

@app.route("/oauth/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "❌ No authorization code received.", 400

    print("\n🔄 Exchanging code for tokens...")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": CODE_VERIFIER,
    }

    auth = (CLIENT_ID, CLIENT_SECRET)
    response = requests.post(TOKEN_URL, data=data, auth=auth)

    if response.status_code == 200:
        tokens = response.json()
        print("\n=== Canva OAuth Tokens ===")
        print("Access Token:", tokens.get("access_token"))
        print("Refresh Token:", tokens.get("refresh_token"))
        print("==========================\n✅ Success! Tokens printed above.")
        return "✅ Authorization successful! Check your console for tokens."
    else:
        print("\n❌ Error during token exchange:", response.text)
        return f"Token exchange failed ({response.status_code}). See console for details."

if __name__ == "__main__":
    print("🚀 Visit http://127.0.0.1:5000 to start Canva authentication.")
    app.run(port=5000)
