"""
dashboard/app.py
Viking AI Drop Catcher — Web Dashboard

Client features:
  - Login with Discord or Google OAuth
  - Add/remove HTTP proxies (host:port or host:port:user:pass)
  - Add/remove Ticketmaster URLs to monitor
  - Set a Discord webhook URL for all their alerts

Admin features (set ADMIN_DISCORD_ID or ADMIN_EMAIL env var):
  - Total clients / total monitored links
  - Top 5 most-watched URLs across all clients
  - Enable / disable clients

Required env vars:
  DISCORD_CLIENT_ID         Discord OAuth2 app client ID
  DISCORD_CLIENT_SECRET     Discord OAuth2 app client secret
  GOOGLE_CLIENT_ID          Google OAuth2 client ID
  GOOGLE_CLIENT_SECRET      Google OAuth2 client secret
  DASHBOARD_SECRET_KEY      Flask session secret (change in production!)
  DASHBOARD_BASE_URL        Public base URL, e.g. https://app.vikingai.io
  ADMIN_DISCORD_ID          Discord snowflake ID of the admin user
  ADMIN_EMAIL               Email of the admin user (Google login)
  VIKING_DB_PATH            SQLite path (shared with drop_catcher)
  DASHBOARD_PORT            Port to listen on (default 5001)
"""
from __future__ import annotations

import os
import sys
import functools
from pathlib import Path

# Allow `import db` to resolve from this directory
sys.path.insert(0, str(Path(__file__).parent))

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

try:
    from authlib.integrations.flask_client import OAuth
    _AUTHLIB = True
except ImportError:
    _AUTHLIB = False

import db as dashboard_db

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.getenv("DASHBOARD_SECRET_KEY", "dev-secret-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

BASE_URL = os.getenv("DASHBOARD_BASE_URL", "http://localhost:5001").rstrip("/")
ADMIN_DISCORD_ID = os.getenv("ADMIN_DISCORD_ID", "").strip()
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()

# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

if _AUTHLIB:
    oauth = OAuth(app)
    oauth.register(
        name="discord",
        client_id=os.getenv("DISCORD_CLIENT_ID"),
        client_secret=os.getenv("DISCORD_CLIENT_SECRET"),
        authorize_url="https://discord.com/oauth2/authorize",
        access_token_url="https://discord.com/api/oauth2/token",
        api_base_url="https://discord.com/api/",
        client_kwargs={"scope": "identify email"},
    )
    oauth.register(
        name="google",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
else:
    oauth = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def current_user():
    return session.get("user")


def is_admin() -> bool:
    user = current_user()
    if not user:
        return False
    return bool(
        (ADMIN_DISCORD_ID and user.get("discord_id") == ADMIN_DISCORD_ID)
        or (ADMIN_EMAIL and user.get("email", "").lower() == ADMIN_EMAIL)
    )


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        if not is_admin():
            flash("Admin access required.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Public / auth routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    if current_user():
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/login")
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/auth/discord")
def auth_discord():
    if not oauth:
        flash("OAuth library not installed (pip install authlib).", "error")
        return redirect(url_for("login"))
    redirect_uri = f"{BASE_URL}/auth/discord/callback"
    return oauth.discord.authorize_redirect(redirect_uri)


@app.route("/auth/discord/callback")
def auth_discord_callback():
    if not oauth:
        flash("OAuth not configured.", "error")
        return redirect(url_for("login"))
    try:
        token = oauth.discord.authorize_access_token()
        resp = oauth.discord.get("users/@me", token=token)
        u = resp.json()

        discord_id = str(u.get("id", ""))
        email = u.get("email", "")
        username = u.get("username", "")
        display_name = u.get("global_name") or username
        avatar_hash = u.get("avatar")
        avatar_url = (
            f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png"
            if avatar_hash
            else "https://cdn.discordapp.com/embed/avatars/0.png"
        )

        client = dashboard_db.upsert_client(
            discord_id=discord_id,
            email=email,
            display_name=display_name,
            avatar_url=avatar_url,
        )
        _set_session(client, discord_id=discord_id)
        return redirect(url_for("dashboard"))
    except Exception as exc:
        flash(f"Discord login failed: {exc}", "error")
        return redirect(url_for("login"))


@app.route("/auth/google")
def auth_google():
    if not oauth:
        flash("OAuth library not installed (pip install authlib).", "error")
        return redirect(url_for("login"))
    redirect_uri = f"{BASE_URL}/auth/google/callback"
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/auth/google/callback")
def auth_google_callback():
    if not oauth:
        flash("OAuth not configured.", "error")
        return redirect(url_for("login"))
    try:
        token = oauth.google.authorize_access_token()
        u = token.get("userinfo") or {}
        if not u:
            u = oauth.google.userinfo(token=token)

        google_id = str(u.get("sub", ""))
        email = u.get("email", "")
        display_name = u.get("name", email)
        avatar_url = u.get("picture", "")

        client = dashboard_db.upsert_client(
            google_id=google_id,
            email=email,
            display_name=display_name,
            avatar_url=avatar_url,
        )
        _set_session(client, google_id=google_id)
        return redirect(url_for("dashboard"))
    except Exception as exc:
        flash(f"Google login failed: {exc}", "error")
        return redirect(url_for("login"))


def _set_session(client, discord_id: str = "", google_id: str = "") -> None:
    session["user"] = {
        "id": client["id"],
        "discord_id": discord_id,
        "google_id": google_id,
        "email": client["email"] or "",
        "display_name": client["display_name"] or "User",
        "avatar_url": client["avatar_url"] or "",
    }


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Client dashboard
# ---------------------------------------------------------------------------


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    client_id = user["id"]
    proxies = [dict(p) for p in dashboard_db.get_proxies(client_id)]
    monitors = [dict(m) for m in dashboard_db.get_monitors(client_id)]

    # Pull the current global webhook (first non-empty value)
    current_webhook = next(
        (m["discord_webhook"] for m in monitors if m.get("discord_webhook")), ""
    )

    return render_template(
        "dashboard.html",
        user=user,
        proxies=proxies,
        monitors=monitors,
        current_webhook=current_webhook,
        is_admin=is_admin(),
    )


@app.route("/api/proxy/add", methods=["POST"])
@login_required
def api_proxy_add():
    user = current_user()
    proxy = (request.form.get("proxy") or "").strip()
    if not proxy:
        flash("Proxy cannot be empty.", "error")
        return redirect(url_for("dashboard") + "#proxies")
    parts = proxy.split(":")
    if len(parts) < 2:
        flash("Invalid format. Use host:port or host:port:user:pass", "error")
        return redirect(url_for("dashboard") + "#proxies")
    dashboard_db.add_proxy(user["id"], proxy)
    flash("Proxy added.", "success")
    return redirect(url_for("dashboard") + "#proxies")


@app.route("/api/proxy/delete/<int:proxy_id>", methods=["POST"])
@login_required
def api_proxy_delete(proxy_id):
    dashboard_db.delete_proxy(proxy_id, current_user()["id"])
    flash("Proxy removed.", "success")
    return redirect(url_for("dashboard") + "#proxies")


@app.route("/api/monitor/add", methods=["POST"])
@login_required
def api_monitor_add():
    user = current_user()
    url = (request.form.get("url") or "").strip()
    label = (request.form.get("label") or "").strip()
    webhook = (request.form.get("discord_webhook") or "").strip()

    if not url:
        flash("URL cannot be empty.", "error")
        return redirect(url_for("dashboard") + "#monitors")
    if not url.startswith("http"):
        flash("URL must start with http:// or https://", "error")
        return redirect(url_for("dashboard") + "#monitors")

    # Inherit global webhook if not specified per-monitor
    if not webhook:
        monitors = dashboard_db.get_monitors(user["id"])
        webhook = next(
            (m["discord_webhook"] for m in monitors if m["discord_webhook"]), ""
        )

    dashboard_db.add_monitor(user["id"], url, label, webhook)
    flash("Monitor added.", "success")
    return redirect(url_for("dashboard") + "#monitors")


@app.route("/api/monitor/delete/<int:monitor_id>", methods=["POST"])
@login_required
def api_monitor_delete(monitor_id):
    dashboard_db.delete_monitor(monitor_id, current_user()["id"])
    flash("Monitor removed.", "success")
    return redirect(url_for("dashboard") + "#monitors")


@app.route("/api/webhook/save", methods=["POST"])
@login_required
def api_webhook_save():
    user = current_user()
    webhook = (request.form.get("discord_webhook") or "").strip()
    if webhook and not webhook.startswith("https://discord.com/api/webhooks/"):
        flash("That doesn't look like a valid Discord webhook URL.", "error")
        return redirect(url_for("dashboard") + "#webhook")
    dashboard_db.set_global_webhook(user["id"], webhook)
    flash("Discord webhook saved and applied to all monitors.", "success")
    return redirect(url_for("dashboard") + "#webhook")


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------


@app.route("/admin")
@admin_required
def admin():
    stats = dashboard_db.get_admin_stats()
    return render_template(
        "admin.html",
        user=current_user(),
        stats=stats,
        is_admin=True,
    )


@app.route("/admin/client/<int:client_id>/toggle", methods=["POST"])
@admin_required
def admin_toggle_client(client_id):
    active = int(request.form.get("active", 1))
    dashboard_db.toggle_client(client_id, active)
    flash(f"Client {'enabled' if active else 'disabled'}.", "success")
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    dashboard_db.init_db()
    port = int(os.getenv("DASHBOARD_PORT", "5001"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    print(f"Viking AI Dashboard running on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
