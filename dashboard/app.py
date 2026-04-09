"""
dashboard/app.py
Viking AI Drop Catcher — Web Dashboard

Client features:
  - Login with Discord or Google OAuth
  - Invite code redemption (assigns subscription tier)
  - Add/remove HTTP proxies (host:port or host:port:user:pass)
  - Add/remove Ticketmaster URLs to monitor (capped by tier)
  - Set a Discord webhook URL for all alerts
  - Hard lockout on expired/canceled subscription

Admin features (ADMIN_DISCORD_ID or ADMIN_EMAIL env var):
  - Overview: total clients, monitors, top 5 URLs, tier breakdown
  - Client detail + tier/day management
  - Invite code manager (create, extend, deactivate)
  - Audit log
  - Broadcast DM via bot

Stripe: POST /stripe/webhook receives Stripe events, syncs subscription status.

Required env vars:
  DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET
  GOOGLE_CLIENT_ID  / GOOGLE_CLIENT_SECRET
  DASHBOARD_SECRET_KEY      Flask session secret
  DASHBOARD_BASE_URL        e.g. https://app.vikingai.io
  ADMIN_DISCORD_ID          Discord snowflake ID of admin
  ADMIN_EMAIL               Email of admin (Google login)
  STRIPE_WEBHOOK_SECRET     whsec_... from Stripe dashboard
  STRIPE_STARTER_PRICE_ID / STRIPE_PRO_PRICE_ID / STRIPE_UNLIMITED_PRICE_ID
  VIKING_DB_PATH            SQLite path (shared with drop_catcher)
  DASHBOARD_PORT            Port (default 5001)
"""
from __future__ import annotations

import functools
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import re

from flask import (
    Flask, flash, redirect, render_template,
    request, session, url_for,
)

try:
    from authlib.integrations.flask_client import OAuth
    _AUTHLIB = True
except ImportError:
    _AUTHLIB = False

import db as dashboard_db
import stripe_handler as _stripe_handler

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "")
if not _SECRET_KEY:
    import warnings
    warnings.warn(
        "DASHBOARD_SECRET_KEY not set — using insecure default. Set this env var before deploying.",
        stacklevel=1,
    )
    _SECRET_KEY = "dev-secret-insecure-change-before-deploy"

app = Flask(__name__)
app.secret_key = _SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

BASE_URL         = os.getenv("DASHBOARD_BASE_URL", "http://localhost:5001").rstrip("/")
ADMIN_DISCORD_ID = os.getenv("ADMIN_DISCORD_ID", "").strip()
ADMIN_EMAIL      = os.getenv("ADMIN_EMAIL", "").strip().lower()

# Jinja filter — format unix timestamp
@app.template_filter("ts")
def _ts_filter(value):
    if not value:
        return "—"
    try:
        return datetime.utcfromtimestamp(float(value)).strftime("%b %d, %Y")
    except Exception:
        return "—"

@app.template_filter("ts_full")
def _ts_full_filter(value):
    if not value:
        return "—"
    try:
        return datetime.utcfromtimestamp(float(value)).strftime("%b %d, %Y %H:%M UTC")
    except Exception:
        return "—"

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


def _set_session(client, discord_id: str = "", google_id: str = "") -> None:
    session["user"] = {
        "id":           client["id"],
        "discord_id":   discord_id,
        "google_id":    google_id,
        "email":        client["email"] or "",
        "display_name": client["display_name"] or "User",
        "avatar_url":   client["avatar_url"] or "",
    }


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def active_sub_required(f):
    """Redirect to /expired if subscription is not active. Admins bypass."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        if not is_admin() and not dashboard_db.is_subscription_active(user["id"]):
            # Testers with no current_period_end are always active
            sub = dashboard_db.get_subscription(user["id"])
            if sub and sub.get("current_period_end"):
                return redirect(url_for("expired"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        if not is_admin():
            # Return 404 — admin routes invisible to non-admins
            return app.response_class(status=404)
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Auth routes
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
        flash("OAuth library not installed.", "error")
        return redirect(url_for("login"))
    return oauth.discord.authorize_redirect(f"{BASE_URL}/auth/discord/callback")


@app.route("/auth/discord/callback")
def auth_discord_callback():
    if not oauth:
        flash("OAuth not configured.", "error")
        return redirect(url_for("login"))
    try:
        token = oauth.discord.authorize_access_token()
        u = oauth.discord.get("users/@me", token=token).json()
        if not u.get("id"):
            raise ValueError("Discord OAuth returned no user ID")

        discord_id  = str(u.get("id", ""))
        email       = u.get("email", "")
        username    = u.get("username", "")
        display_name = u.get("global_name") or username
        avatar_hash = u.get("avatar")
        avatar_url  = (
            f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png"
            if avatar_hash else
            "https://cdn.discordapp.com/embed/avatars/0.png"
        )

        client = dashboard_db.upsert_client(
            discord_id=discord_id, email=email,
            display_name=display_name, avatar_url=avatar_url,
        )
        dashboard_db.ensure_subscription(client["id"])
        _set_session(client, discord_id=discord_id)
        return redirect(url_for("dashboard"))
    except Exception as exc:
        flash(f"Discord login failed: {exc}", "error")
        return redirect(url_for("login"))


@app.route("/auth/google")
def auth_google():
    if not oauth:
        flash("OAuth library not installed.", "error")
        return redirect(url_for("login"))
    return oauth.google.authorize_redirect(f"{BASE_URL}/auth/google/callback")


@app.route("/auth/google/callback")
def auth_google_callback():
    if not oauth:
        flash("OAuth not configured.", "error")
        return redirect(url_for("login"))
    try:
        token = oauth.google.authorize_access_token()
        u = token.get("userinfo") or {}
        if not u.get("sub"):
            raise ValueError("Google OAuth returned no user ID")

        google_id    = str(u.get("sub", ""))
        email        = u.get("email", "")
        display_name = u.get("name", email)
        avatar_url   = u.get("picture", "")

        client = dashboard_db.upsert_client(
            google_id=google_id, email=email,
            display_name=display_name, avatar_url=avatar_url,
        )
        dashboard_db.ensure_subscription(client["id"])
        _set_session(client, google_id=google_id)
        return redirect(url_for("dashboard"))
    except Exception as exc:
        flash(f"Google login failed: {exc}", "error")
        return redirect(url_for("login"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Expired / lockout page
# ---------------------------------------------------------------------------

@app.route("/expired")
@login_required
def expired():
    user = current_user()
    sub  = dashboard_db.get_subscription(user["id"])
    return render_template("expired.html", user=user, sub=sub, is_admin=False)


# ---------------------------------------------------------------------------
# Invite code redemption
# ---------------------------------------------------------------------------

@app.route("/redeem", methods=["GET", "POST"])
@login_required
def redeem():
    user = current_user()
    if request.method == "POST":
        code = (request.form.get("code") or "").strip().upper()
        # Store and compare case-insensitively by normalising on creation too
        ok, msg = dashboard_db.redeem_invite_code(code, user["id"])
        flash(msg, "success" if ok else "error")
        if ok:
            return redirect(url_for("dashboard"))
    sub = dashboard_db.get_subscription(user["id"])
    return render_template("redeem.html", user=user, sub=sub, is_admin=is_admin())


# ---------------------------------------------------------------------------
# Client dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
@active_sub_required
def dashboard():
    user      = current_user()
    client_id = user["id"]
    sub       = dashboard_db.ensure_subscription(client_id)
    proxies   = [dict(p) for p in dashboard_db.get_proxies(client_id)]
    monitors  = [dict(m) for m in dashboard_db.get_monitors(client_id)]
    limit     = dashboard_db.get_monitor_limit(client_id)

    current_webhook = next(
        (m["discord_webhook"] for m in monitors if m.get("discord_webhook")), ""
    )

    # Days remaining calculation
    days_left = None
    if sub.get("current_period_end"):
        extra = sub.get("extra_days", 0) or 0
        remaining = (sub["current_period_end"] + extra * 86400) - time.time()
        days_left = max(0, int(remaining / 86400))

    hot_picks         = dashboard_db.get_hot_picks(active_only=True)
    personal_watchlist = dashboard_db.get_personal_watchlist(client_id)
    watchlist_limit   = dashboard_db.get_watchlist_limit(client_id)

    return render_template(
        "dashboard.html",
        user=user,
        sub=sub,
        proxies=proxies,
        monitors=monitors,
        current_webhook=current_webhook,
        monitor_limit=limit,
        days_left=days_left,
        is_admin=is_admin(),
        hot_picks=hot_picks,
        personal_watchlist=personal_watchlist,
        watchlist_limit=watchlist_limit,
    )


@app.route("/api/invite/redeem", methods=["POST"])
@login_required
def api_invite_redeem():
    user = current_user()
    code = (request.form.get("code") or "").strip()
    ok, msg = dashboard_db.redeem_invite_code(code, user["id"])
    flash(msg, "success" if ok else "error")
    return redirect(url_for("dashboard"))


@app.route("/api/proxy/add", methods=["POST"])
@login_required
@active_sub_required
def api_proxy_add():
    user  = current_user()
    proxy = (request.form.get("proxy") or "").strip()
    if not proxy or len(proxy.split(":")) < 2:
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
@active_sub_required
def api_monitor_add():
    user      = current_user()
    url       = (request.form.get("url") or "").strip()
    label     = (request.form.get("label") or "").strip()
    webhook   = (request.form.get("discord_webhook") or "").strip()

    if not url or not url.startswith("http"):
        flash("A valid URL is required (must start with http).", "error")
        return redirect(url_for("dashboard") + "#monitors")

    # Tier limit check
    limit = dashboard_db.get_monitor_limit(user["id"])
    if limit is not None:
        current_count = len(dashboard_db.get_monitors(user["id"]))
        if current_count >= limit:
            flash(
                f"Your plan allows a maximum of {limit} monitors. "
                "Upgrade to add more.", "error"
            )
            return redirect(url_for("dashboard") + "#monitors")

    # Inherit global webhook if none specified
    if not webhook:
        for m in dashboard_db.get_monitors(user["id"]):
            if m["discord_webhook"]:
                webhook = m["discord_webhook"]
                break

    # Auto-detect platform from URL
    url_lower = url.lower()
    if "seatgeek.com" in url_lower:
        platform = "seatgeek"
    elif "stubhub.com" in url_lower:
        platform = "stubhub"
    elif "vividseats.com" in url_lower:
        platform = "vividseats"
    else:
        platform = "ticketmaster"

    dashboard_db.add_monitor(user["id"], url, label, webhook, platform=platform)
    flash(f"Monitor added ({platform.title()}).", "success")
    return redirect(url_for("dashboard") + "#monitors")


@app.route("/api/monitor/delete/<int:monitor_id>", methods=["POST"])
@login_required
def api_monitor_delete(monitor_id):
    dashboard_db.delete_monitor(monitor_id, current_user()["id"])
    flash("Monitor removed.", "success")
    return redirect(url_for("dashboard") + "#monitors")


@app.route("/api/webhook/save", methods=["POST"])
@login_required
@active_sub_required
def api_webhook_save():
    user    = current_user()
    webhook = (request.form.get("discord_webhook") or "").strip()
    _webhook_pattern = r"^https://discord\.com/api/webhooks/\d+/[A-Za-z0-9_\-]+$"
    if webhook and not re.match(_webhook_pattern, webhook):
        flash("Invalid Discord webhook URL. Expected: https://discord.com/api/webhooks/ID/TOKEN", "error")
        return redirect(url_for("dashboard") + "#webhook")
    dashboard_db.set_global_webhook(user["id"], webhook)
    flash("Discord webhook saved and applied to all monitors.", "success")
    return redirect(url_for("dashboard") + "#webhook")


# ---------------------------------------------------------------------------
# Personal watchlist
# ---------------------------------------------------------------------------

@app.route("/api/watchlist/add", methods=["POST"])
@login_required
@active_sub_required
def api_watchlist_add():
    user   = current_user()
    artist = (request.form.get("artist") or "").strip()
    if not artist:
        flash("Artist name is required.", "error")
        return redirect(url_for("dashboard") + "#watchlist")
    ok, msg = dashboard_db.add_personal_watchlist(user["id"], artist)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("dashboard") + "#watchlist")


@app.route("/api/watchlist/remove/<int:entry_id>", methods=["POST"])
@login_required
def api_watchlist_remove(entry_id):
    dashboard_db.remove_personal_watchlist(entry_id, current_user()["id"])
    flash("Removed from watchlist.", "success")
    return redirect(url_for("dashboard") + "#watchlist")


# ---------------------------------------------------------------------------
# Stripe webhook
# ---------------------------------------------------------------------------

@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")
    ok, msg    = _stripe_handler.handle_webhook(payload, sig_header)
    if not ok:
        return {"error": msg}, 400
    return {"status": "ok"}, 200


# ---------------------------------------------------------------------------
# Admin — all routes return 404 to non-admins (invisible)
# ---------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin():
    stats           = dashboard_db.get_admin_stats()
    hot_picks       = dashboard_db.get_hot_picks(active_only=False)
    global_watchlist = dashboard_db.get_global_watchlist(active_only=False)
    return render_template(
        "admin.html",
        user=current_user(),
        stats=stats,
        hot_picks=hot_picks,
        global_watchlist=global_watchlist,
        is_admin=True,
    )


@app.route("/admin/client/<int:client_id>")
@admin_required
def admin_client_detail(client_id):
    detail = dashboard_db.get_client_detail(client_id)
    if not detail:
        flash("Client not found.", "error")
        return redirect(url_for("admin"))
    return render_template(
        "admin_client.html",
        user=current_user(),
        detail=detail,
        tiers=list(dashboard_db.TIER_LIMITS.keys()),
        is_admin=True,
    )


@app.route("/admin/client/<int:client_id>/toggle", methods=["POST"])
@admin_required
def admin_toggle_client(client_id):
    active = int(request.form.get("active", 1))
    dashboard_db.toggle_client(client_id, active)
    flash(f"Client {'enabled' if active else 'disabled'}.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/client/<int:client_id>/set-tier", methods=["POST"])
@admin_required
def admin_set_tier(client_id):
    tier = (request.form.get("tier") or "tester").strip()
    if tier not in dashboard_db.TIER_LIMITS:
        flash("Invalid tier.", "error")
        return redirect(url_for("admin_client_detail", client_id=client_id))
    admin_name = current_user().get("display_name", "admin")
    dashboard_db.admin_set_tier(client_id, tier, admin_name)
    flash(f"Tier updated to {tier}.", "success")
    return redirect(url_for("admin_client_detail", client_id=client_id))


@app.route("/admin/client/<int:client_id>/add-days", methods=["POST"])
@admin_required
def admin_add_days(client_id):
    try:
        days = int(request.form.get("days", 0))
    except ValueError:
        days = 0
    if days <= 0:
        flash("Days must be a positive number.", "error")
        return redirect(url_for("admin_client_detail", client_id=client_id))
    admin_name = current_user().get("display_name", "admin")
    dashboard_db.admin_add_days(client_id, days, admin_name)
    flash(f"+{days} days added to subscription.", "success")
    return redirect(url_for("admin_client_detail", client_id=client_id))


@app.route("/admin/client/<int:client_id>/pause-monitors", methods=["POST"])
@admin_required
def admin_pause_monitors(client_id):
    paused     = int(request.form.get("paused", 1))
    admin_name = current_user().get("display_name", "admin")
    dashboard_db.admin_pause_monitors(client_id, paused, admin_name)
    flash(f"Monitors {'paused' if paused else 'resumed'}.", "success")
    return redirect(url_for("admin_client_detail", client_id=client_id))


# --- Invite code management ---

@app.route("/admin/invite/create", methods=["POST"])
@admin_required
def admin_invite_create():
    tier      = (request.form.get("tier") or "tester").strip()
    max_uses  = max(1, int(request.form.get("max_uses", 1) or 1))
    days_str  = (request.form.get("expires_days") or "").strip()
    note      = (request.form.get("note") or "").strip()[:200]
    expires_days = int(days_str) if days_str.isdigit() else None

    code = dashboard_db.create_invite_code(
        tier=tier, max_uses=max_uses,
        expires_days=expires_days, note=note,
    )
    flash(f"Invite code created: {code}", "success")
    return redirect(url_for("admin") + "#invites")


@app.route("/admin/invite/<code>/deactivate", methods=["POST"])
@admin_required
def admin_invite_deactivate(code):
    admin_name = current_user().get("display_name", "admin")
    dashboard_db.deactivate_invite_code(code, admin_name)
    flash(f"Code {code} deactivated.", "success")
    return redirect(url_for("admin") + "#invites")


@app.route("/admin/invite/<code>/extend", methods=["POST"])
@admin_required
def admin_invite_extend(code):
    try:
        days = int(request.form.get("days", 7))
    except ValueError:
        days = 7
    admin_name = current_user().get("display_name", "admin")
    dashboard_db.extend_invite_code(code, days, admin_name)
    flash(f"Code {code} extended by {days} days.", "success")
    return redirect(url_for("admin") + "#invites")


# --- Hot picks management ---

@app.route("/admin/hotpick/add", methods=["POST"])
@admin_required
def admin_hotpick_add():
    artist    = (request.form.get("artist") or "").strip()
    note      = (request.form.get("note") or "").strip()[:300]
    image_url = (request.form.get("image_url") or "").strip()
    if not artist:
        flash("Artist name is required.", "error")
        return redirect(url_for("admin") + "#hotpicks")
    dashboard_db.admin_add_hot_pick(artist, note, image_url)
    flash(f"Hot pick added: {artist}", "success")
    return redirect(url_for("admin") + "#hotpicks")


@app.route("/admin/hotpick/<int:pick_id>/deactivate", methods=["POST"])
@admin_required
def admin_hotpick_deactivate(pick_id):
    admin_name = current_user().get("display_name", "admin")
    dashboard_db.admin_deactivate_hot_pick(pick_id, admin_name)
    flash("Hot pick removed.", "success")
    return redirect(url_for("admin") + "#hotpicks")


# --- Global watchlist management ---

@app.route("/admin/watchlist/add", methods=["POST"])
@admin_required
def admin_watchlist_add():
    artist    = (request.form.get("artist") or "").strip()
    note      = (request.form.get("note") or "").strip()[:200]
    if not artist:
        flash("Artist name is required.", "error")
        return redirect(url_for("admin") + "#watchlist")
    dashboard_db.admin_add_global_watchlist(artist, note)
    flash(f"Added {artist} to global watchlist.", "success")
    return redirect(url_for("admin") + "#watchlist")


@app.route("/admin/watchlist/remove/<int:wl_id>", methods=["POST"])
@admin_required
def admin_watchlist_remove(wl_id):
    admin_name = current_user().get("display_name", "admin")
    dashboard_db.admin_remove_global_watchlist(wl_id, admin_name)
    flash("Removed from global watchlist.", "success")
    return redirect(url_for("admin") + "#watchlist")


# ---------------------------------------------------------------------------
# Price history API
# ---------------------------------------------------------------------------

@app.route("/api/monitors/<int:monitor_id>/price_history")
@active_sub_required
def api_price_history(monitor_id):
    """Return up to 50 price snapshots for a monitor as JSON.
    Only returns data if the monitor belongs to the logged-in client.
    """
    client = current_user()
    # Verify ownership
    with dashboard_db.connect() as conn:
        row = conn.execute(
            "SELECT id FROM dc_monitors WHERE id=? AND client_id=?",
            (monitor_id, client["id"]),
        ).fetchone()
    if not row:
        return {"error": "not found"}, 404

    history = dashboard_db.get_price_history(monitor_id, limit=50)
    return {"monitor_id": monitor_id, "history": history}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    dashboard_db.init_db()
    port  = int(os.getenv("DASHBOARD_PORT", "5001"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    print(f"Viking AI Dashboard → http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
