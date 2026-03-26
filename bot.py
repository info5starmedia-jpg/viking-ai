#!/usr/bin/env python3
"""
Viking AI Discord Bot (v1.0)

Rules:
- Single .env source of truth: /opt/viking-ai/.env (gitignored). No .env.example.
- Resilient imports: missing optional modules should not crash the bot.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import datetime as _dt

import discord
from discord import app_commands
from dotenv import load_dotenv

try:
    from embed_builder import build_alert, AlertView
    _EMBED_BUILDER_OK = True
except Exception as _eb_err:
    _EMBED_BUILDER_OK = False
    logging.getLogger(__name__).warning("embed_builder not available: %s", _eb_err)

# ---------------------------------------------------------------------
# .env (single source of truth)
# ---------------------------------------------------------------------
load_dotenv("/opt/viking-ai/.env", override=False)

DISCORD_TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
if not DISCORD_TOKEN:
    raise SystemExit("DISCORD_TOKEN is missing in /opt/viking-ai/.env")

GUILD_ID_STR = (os.getenv("GUILD_ID") or "").strip()
GUILD_ID: int = int(GUILD_ID_STR) if GUILD_ID_STR.isdigit() else 0

# Optional routing (channels or webhooks; none required)
DEFAULT_CHANNEL_ID = int((os.getenv("DISCORD_CHANNEL_ID") or "0").strip() or 0)

PRICE_ALERT_CHANNEL_ID = int((os.getenv("PRICE_ALERT_CHANNEL_ID") or "0").strip() or 0)
VERIFIED_FAN_ALERT_CHANNEL_ID = int((os.getenv("VERIFIED_FAN_ALERT_CHANNEL_ID") or "0").strip() or 0)
TOUR_SCAN_ALERT_CHANNEL_ID = int((os.getenv("TOUR_SCAN_ALERT_CHANNEL_ID") or "0").strip() or 0)

PRICE_WEBHOOK_URL = (os.getenv("PRICE_WEBHOOK_URL") or "").strip()
VERIFIED_FAN_WEBHOOK_URL = (os.getenv("VERIFIED_FAN_WEBHOOK_URL") or "").strip()
TOUR_SCAN_WEBHOOK_URL = (os.getenv("TOUR_SCAN_WEBHOOK_URL") or "").strip()

# Polling intervals (seconds)
PRICE_POLL_SECONDS = int((os.getenv("PRICE_POLL_SECONDS") or "900").strip() or 900)
VERIFIED_FAN_POLL_SECONDS = int((os.getenv("VERIFIED_FAN_POLL_SECONDS") or "7200").strip() or 7200)
TOUR_SCAN_POLL_SECONDS = int((os.getenv("TOUR_SCAN_POLL_SECONDS") or "3600").strip() or 3600)
INTEL_REFRESH_SECONDS = int((os.getenv("INTEL_REFRESH_SECONDS") or "21600").strip() or 21600)

# A/B tour output mode:
# - "fast" => headline + top cities + why (short)
# - "full" => full intel style (events + on-sale placeholders + sellout score)
TOUR_SCAN_MODE = (os.getenv("TOUR_SCAN_MODE") or "fast").strip().lower()

# Ticketmaster surge watch poll (seconds). Default 30 min.
TM_SURGE_POLL_SECONDS = int(os.getenv("TM_SURGE_POLL_SECONDS", "1800") or "1800")

# Drop catcher poll (seconds). Default 5 min.
DROP_CATCHER_POLL_SECONDS = int(os.getenv("DROP_CATCHER_POLL_SECONDS", "300") or "300")

# Drop catcher alert channel/webhook (falls back to tour channel / default)
DROP_CATCHER_CHANNEL_ID = int((os.getenv("DROP_CATCHER_CHANNEL_ID") or "0").strip() or 0)
DROP_CATCHER_WEBHOOK_URL = (os.getenv("DROP_CATCHER_WEBHOOK_URL") or "").strip()

# Daily digest — UTC hour to post (default 9 = 9:00 AM UTC)
DROP_DIGEST_HOUR_UTC = int(os.getenv("DROP_DIGEST_HOUR_UTC", "9") or "9")
DROP_DIGEST_ENABLED = os.getenv("DROP_DIGEST_ENABLED", "1").strip().lower() not in ("0", "false", "")

# Optional: explicit git revision (can be injected by CI)
GIT_REV = (os.getenv("GIT_REV") or "").strip()

# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("viking_ai")

START_UNIX = time.time()

# ---------------------------------------------------------------------
# Best-effort optional imports (never crash on missing)
# ---------------------------------------------------------------------
def _try_import(name: str):
    try:
        return __import__(name)
    except Exception as e:
        logger.info("Optional import skipped: %s (%s)", name, e)
        return None

price_monitor = _try_import("price_monitor")
verified_fan_monitor = _try_import("verified_fan_monitor")
tour_scan_monitor = _try_import("tour_scan_monitor")
tm_surge_watch = _try_import("tm_surge_watch")
drop_catcher = _try_import("drop_catcher")
platform_monitor = _try_import("platform_monitor")
usage_db = _try_import("usage_db")

ticketmaster_agent = _try_import("ticketmaster_agent")
tour_news_agent_v3 = _try_import("tour_news_agent_v3")
tour_intel_agent = _try_import("tour_intel_agent")

spotify_agent = _try_import("spotify_agent")
youtube_agent = _try_import("youtube_agent")
tiktok_agent = _try_import("tiktok_agent")
streaming_metrics = _try_import("streaming_metrics")
socials_agent = _try_import("socials_agent")

city_boosts = _try_import("city_boosts")

# ---------------------------------------------------------------------
# Discord client + tree
# ---------------------------------------------------------------------
INTENTS = discord.Intents.default()
client = discord.Client(intents=INTENTS)
tree = app_commands.CommandTree(client)


# Dismiss button interaction — handled globally via on_interaction
@client.event
async def on_interaction(interaction: discord.Interaction) -> None:
    if interaction.type != discord.InteractionType.component:
        return
    data = interaction.data or {}
    if data.get("custom_id") == "viking_alert_dismiss":
        try:
            await interaction.response.edit_message(
                content="*(alert dismissed)*",
                embed=None,
                view=None,
            )
        except discord.NotFound:
            pass
        except Exception as exc:
            logger.debug("dismiss interaction error: %s", exc)
            try:
                await interaction.response.send_message("Dismissed.", ephemeral=True)
            except Exception:
                pass

# Background tasks registry
TASKS: Dict[str, asyncio.Task] = {}

# Runtime status snapshot
STATUS: Dict[str, Any] = {
    "start_unix": START_UNIX,
    "last_error": None,
    "sync": {
        "target": None,
        "last_sync_unix": None,
        "last_sync_count": 0,
        "last_sync_ok": False,
        "last_sync_error": None,
    },
    "monitors": {
        "price_monitor": bool(price_monitor),
        "verified_fan_monitor": bool(verified_fan_monitor),
        "tour_scan_monitor": bool(tour_scan_monitor),
        "tm_surge_watch": bool(tm_surge_watch and getattr(tm_surge_watch, "is_available", lambda: False)()),
        "drop_catcher": bool(drop_catcher and getattr(drop_catcher, "is_available", lambda: False)()),
        "platform_monitor": bool(platform_monitor and getattr(platform_monitor, "is_available", lambda: False)()),
    },
    "last_posts": {
        "price_unix": None,
        "vf_unix": None,
        "tour_unix": None,
        "tm_surge_unix": None,
        "drop_catcher_unix": None,
        "platform_monitor_unix": None,
    },
    "tasks": {},
}

def _safe_truncate(s: str, max_len: int = 1800) -> str:
    if s is None:
        return ""
    s = str(s)
    if len(s) <= max_len:
        return s
    return s[: max_len - 12] + "\n…(truncated)"

def _uptime_seconds() -> int:
    return int(time.time() - START_UNIX)

def _rss_mb() -> float:
    # Linux-only best effort
    try:
        import resource
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(rss_kb / 1024.0, 1)
    except Exception:
        return 0.0

def _git_rev() -> str:
    if GIT_REV:
        return GIT_REV
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=os.path.dirname(__file__))
        return out.decode("utf-8").strip()
    except Exception:
        return "unknown"

# ---------------------------------------------------------------------
# Tier gating
# Tiers: FREE < PRO < ELITE
# Default (no row in guild_tiers): PRO — open access during beta
# ---------------------------------------------------------------------
_TIER_ORDER = {"FREE": 0, "PRO": 1, "ELITE": 2}
_DEFAULT_TIER = "PRO"


def _get_guild_tier(guild_id: Optional[int]) -> str:
    if not guild_id or not usage_db:
        return _DEFAULT_TIER
    try:
        override = usage_db.get_guild_tier_override(str(guild_id))
        return override if override in _TIER_ORDER else _DEFAULT_TIER
    except Exception:
        return _DEFAULT_TIER


async def _tier_gate(interaction: discord.Interaction, required: str) -> bool:
    """
    Returns True if the guild meets the required tier. Sends an ephemeral
    denial and returns False if not.

    Usage:
        if not await _tier_gate(interaction, "PRO"):
            return
    """
    guild_id = interaction.guild_id
    current = _get_guild_tier(guild_id)
    current_rank = _TIER_ORDER.get(current, 1)
    required_rank = _TIER_ORDER.get(required.upper(), max(_TIER_ORDER.values()))

    if current_rank >= required_rank:
        return True

    msg = (
        f"🔒 This command requires **{required}** tier "
        f"(your server is on **{current}**). "
        f"Contact the admin to upgrade."
    )
    await _send_ephemeral(interaction, msg)
    return False


async def _get_channel(channel_id: int) -> Optional[discord.abc.Messageable]:
    if not channel_id:
        return None
    ch = client.get_channel(channel_id)
    if ch:
        return ch
    try:
        return await client.fetch_channel(channel_id)
    except Exception:
        return None

async def _send_webhook(url: str, content: str) -> None:
    try:
        wh = discord.Webhook.from_url(url, client=client)
        await wh.send(content=content)
    except Exception as e:
        logger.warning("Webhook send failed: %s", e)


async def _send_webhook_embed(
    url: str,
    embed: "discord.Embed",
    view: "Optional[discord.ui.View]" = None,
    content: str = "",
) -> None:
    """Send an embed (+ optional link-only view) via a Discord webhook URL."""
    try:
        wh = discord.Webhook.from_url(url, client=client)
        kwargs: Dict[str, Any] = {"embed": embed}
        if content:
            kwargs["content"] = content
        if view:
            kwargs["view"] = view
        await wh.send(**kwargs)
    except Exception as e:
        logger.warning("Webhook embed send failed: %s", e)


async def post_rich(
    embed: "discord.Embed",
    view: "Optional[discord.ui.View]" = None,
    content: str = "",
    *,
    prefer: str = "drop",
) -> None:
    """
    Post a rich embed + optional view to the configured drop/alert destination.
    Falls back to post_message(content) if embed_builder is unavailable.
    prefer: default | price | vf | tour | drop
    """
    _webhook_urls = {
        "price": PRICE_WEBHOOK_URL,
        "vf":    VERIFIED_FAN_WEBHOOK_URL,
        "tour":  TOUR_SCAN_WEBHOOK_URL,
        "drop":  DROP_CATCHER_WEBHOOK_URL,
    }
    wh_url = _webhook_urls.get(prefer, "")
    if wh_url:
        # Webhooks: use link-only view (no interaction callback)
        link_view = None
        if view:
            link_view = discord.ui.View()
            for child in view.children:
                if isinstance(child, discord.ui.Button) and child.url:
                    link_view.add_item(
                        discord.ui.Button(label=child.label, url=child.url,
                                          style=discord.ButtonStyle.link)
                    )
        await _send_webhook_embed(wh_url, embed, link_view or None, content)
        return

    _channel_ids = {
        "price": PRICE_ALERT_CHANNEL_ID,
        "vf":    VERIFIED_FAN_ALERT_CHANNEL_ID,
        "tour":  TOUR_SCAN_ALERT_CHANNEL_ID,
        "drop":  DROP_CATCHER_CHANNEL_ID,
    }
    target_id = _channel_ids.get(prefer, 0) or DEFAULT_CHANNEL_ID
    ch = await _get_channel(target_id)
    if not ch:
        logger.warning("post_rich: no channel configured (%s)", prefer)
        return
    try:
        send_kwargs: Dict[str, Any] = {"embed": embed}
        if content:
            send_kwargs["content"] = content
        if view:
            send_kwargs["view"] = view
        await ch.send(**send_kwargs)
    except Exception as e:
        logger.warning("post_rich channel send failed: %s", e)

async def post_message(content: str, *, prefer: str = "default") -> None:
    """
    prefer: default | price | vf | tour | drop
    Uses webhook if set, else falls back to channel.
    """
    if prefer == "price" and PRICE_WEBHOOK_URL:
        await _send_webhook(PRICE_WEBHOOK_URL, content)
        return
    if prefer == "vf" and VERIFIED_FAN_WEBHOOK_URL:
        await _send_webhook(VERIFIED_FAN_WEBHOOK_URL, content)
        return
    if prefer == "tour" and TOUR_SCAN_WEBHOOK_URL:
        await _send_webhook(TOUR_SCAN_WEBHOOK_URL, content)
        return
    if prefer == "drop" and DROP_CATCHER_WEBHOOK_URL:
        await _send_webhook(DROP_CATCHER_WEBHOOK_URL, content)
        return

    target_channel_id = 0
    if prefer == "price":
        target_channel_id = PRICE_ALERT_CHANNEL_ID or DEFAULT_CHANNEL_ID
    elif prefer == "vf":
        target_channel_id = VERIFIED_FAN_ALERT_CHANNEL_ID or DEFAULT_CHANNEL_ID
    elif prefer == "tour":
        target_channel_id = TOUR_SCAN_ALERT_CHANNEL_ID or DEFAULT_CHANNEL_ID
    elif prefer == "drop":
        target_channel_id = DROP_CATCHER_CHANNEL_ID or DEFAULT_CHANNEL_ID
    else:
        target_channel_id = (
            DEFAULT_CHANNEL_ID
            or PRICE_ALERT_CHANNEL_ID
            or VERIFIED_FAN_ALERT_CHANNEL_ID
            or TOUR_SCAN_ALERT_CHANNEL_ID
            or DROP_CATCHER_CHANNEL_ID
        )

    ch = await _get_channel(target_channel_id)
    if not ch:
        logger.warning("No channel configured to post message (%s).", prefer)
        return
    try:
        await ch.send(content)
    except Exception as e:
        logger.warning("Channel send failed: %s", e)

async def _send_ephemeral(interaction: discord.Interaction, content: str) -> None:
    content = _safe_truncate(content, 1900)
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)

async def _send_to_mixed_channel(content: str, *, prefer: str = "default") -> None:
    # Wrapper kept in case you later add “mixed routing” logic.
    await post_message(content, prefer=prefer)

# ---------------------------------------------------------------------
# Slash command sync logic (guild vs global)
# ---------------------------------------------------------------------
async def sync_slash_commands(reason: str = "startup") -> Tuple[bool, str]:
    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            tree.copy_global_to(guild=guild)
            synced = await tree.sync(guild=guild)
            STATUS["sync"]["target"] = f"guild:{GUILD_ID}"
        else:
            synced = await tree.sync()
            STATUS["sync"]["target"] = "global"

        STATUS["sync"]["last_sync_unix"] = time.time()
        STATUS["sync"]["last_sync_count"] = len(synced)
        STATUS["sync"]["last_sync_ok"] = True
        STATUS["sync"]["last_sync_error"] = None
        logger.info("Slash commands synced to %s (%s) [%s commands].", STATUS["sync"]["target"], reason, len(synced))
        return True, f"ok ({len(synced)} commands)"
    except Exception as e:
        STATUS["sync"]["last_sync_unix"] = time.time()
        STATUS["sync"]["last_sync_ok"] = False
        STATUS["sync"]["last_sync_error"] = str(e)
        logger.warning("Slash sync failed (%s): %s", reason, e)
        return False, str(e)

# ---------------------------------------------------------------------
# Background task guard
# ---------------------------------------------------------------------
def _task_guard(name: str, task: asyncio.Task) -> None:
    TASKS[name] = task
    STATUS["tasks"][name] = {"created_unix": time.time()}

    def _done_callback(t: asyncio.Task) -> None:
        try:
            exc = t.exception()
            if exc:
                STATUS["last_error"] = f"task:{name}: {exc}"
                logger.warning("Task %s crashed: %s", name, exc)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning("Task %s done callback error: %s", name, e)

    task.add_done_callback(_done_callback)

# ---------------------------------------------------------------------
# Price monitor loop (uses price_monitor.poll_prices_once)
# ---------------------------------------------------------------------
def _format_price_alert(item: Dict[str, Any]) -> str:
    title = item.get("title") or item.get("name") or "Price update"
    url = item.get("url") or ""
    cur = item.get("current_price") or item.get("price") or ""
    prev = item.get("previous_price") or ""
    pct = item.get("pct_change")

    parts = [f"💸 **{title}**"]
    if cur != "":
        parts.append(f"Now: `{cur}`")
    if prev != "":
        parts.append(f"Was: `{prev}`")
    if pct is not None:
        try:
            parts.append(f"Change: `{float(pct):+.1f}%`")
        except Exception:
            parts.append(f"Change: `{pct}`")
    if url:
        parts.append(url)
    return "\n".join(parts)

async def _price_monitor_loop() -> None:
    if not price_monitor or not hasattr(price_monitor, "poll_prices_once"):
        logger.info("Price monitor not available (price_monitor.poll_prices_once missing).")
        return

    logger.info("Price monitor loop started (%ss interval).", PRICE_POLL_SECONDS)
    while True:
        try:
            items = await asyncio.to_thread(price_monitor.poll_prices_once)
            if items:
                for it in items:
                    await post_message(_format_price_alert(it), prefer="price")
                    STATUS["last_posts"]["price_unix"] = time.time()
        except Exception as e:
            STATUS["last_error"] = f"price_monitor: {e}"
            logger.warning("Price monitor error: %s", e)
        await asyncio.sleep(max(30, PRICE_POLL_SECONDS))

# ---------------------------------------------------------------------
# Verified Fan loop (uses verified_fan_monitor.poll_verified_fan_once)
# ---------------------------------------------------------------------
async def _post_verified_fan_item(item: Dict[str, Any]) -> None:
    title = item.get("title") or item.get("name") or "Verified Fan"
    url = item.get("url") or ""
    artist = item.get("artist") or ""
    msg = f"✅ **Verified Fan**: {title}"
    if artist:
        msg += f"\nArtist: **{artist}**"
    if url:
        msg += f"\n{url}"
    await post_message(msg, prefer="vf")
    STATUS["last_posts"]["vf_unix"] = time.time()

async def _verified_fan_loop() -> None:
    if not verified_fan_monitor or not hasattr(verified_fan_monitor, "poll_verified_fan_once"):
        logger.info("Verified fan monitor not available (verified_fan_monitor.poll_verified_fan_once missing).")
        return

    logger.info("Verified Fan loop started (%ss interval).", VERIFIED_FAN_POLL_SECONDS)

    def _post(item: Dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(_post_verified_fan_item(item), client.loop)

    while True:
        try:
            # Your module signature supports (post_func, interval_seconds) and loop versions.
            try:
                await asyncio.to_thread(verified_fan_monitor.poll_verified_fan_once, _post, VERIFIED_FAN_POLL_SECONDS)
            except TypeError:
                await asyncio.to_thread(verified_fan_monitor.poll_verified_fan_once, _post)
        except Exception as e:
            STATUS["last_error"] = f"verified_fan: {e}"
            logger.warning("Verified fan error: %s", e)
        await asyncio.sleep(max(60, VERIFIED_FAN_POLL_SECONDS))

# ---------------------------------------------------------------------
# Tour scan background (uses tour_scan_monitor.start_background_thread)
# Adds A/B formatting via TOUR_SCAN_MODE=fast|full
# ---------------------------------------------------------------------
def _tour_fast_message(item: Dict[str, Any]) -> str:
    """
    A) Fast alert (headline + top cities + why, short)
    """
    title = (item.get("title") or "New tour announcement").strip()
    link = (item.get("link") or "").strip()

    # Best-effort: infer artist from title
    artist_guess = title.split(" - ")[0].strip() if " - " in title else title[:60].strip()

    cities = _best_cities_for_artist(artist_guess, limit=5)
    why_bits = []
    if cities:
        # Use components if available
        top = cities[0]
        comp = top.get("components") or {}
        if comp:
            why_bits.append(f"signals={list(comp.keys())[:3]}")
        why_bits.append("high-demand markets")
    why = " • ".join(why_bits) if why_bits else "best-effort demand signals"

    lines = [f"🎤 **Tour announced**: {title}"]
    if cities:
        lines.append("🏙️ Top cities: " + ", ".join([c.get("city") for c in cities if c.get("city")]))
    lines.append(f"Why: {why}")
    if link:
        lines.append(link)
    return _safe_truncate("\n".join([l for l in lines if l]).strip(), 1800)

def _tour_full_intel_message(item: Dict[str, Any]) -> str:
    """
    B) Full intel (includes event list + on-sale placeholders + sellout score)
    Note: On-sale dates require deeper enrichment (TM details / artist site scraping).
    """
    title = (item.get("title") or "New tour announcement").strip()
    link = (item.get("link") or "").strip()
    artist_guess = title.split(" - ")[0].strip() if " - " in title else title[:60].strip()

    stars = _stars_for_artist(artist_guess)
    ranked = _best_cities_for_artist(artist_guess, limit=10)
    events = _tm_events_for_artist(artist_guess, limit=10)

    lines: List[str] = []
    lines.append(f"📣 **New tour intel**: {title}")
    lines.append(f"Artist rating: {_stars_emoji(stars)} ({stars}/5)")
    if link:
        lines.append(link)

    lines.append("")
    lines.append("**Best cities (with reason)**")
    if ranked:
        for r in ranked[:7]:
            city = r.get("city")
            score = r.get("score")
            comp = r.get("components") or {}
            reason = ", ".join(list(comp.keys())[:3]) if comp else "ranking signals"
            lines.append(f"• {city} (score={score}) — {reason}")
    else:
        lines.append("• (no city ranking available)")

    lines.append("")
    lines.append("**Events (Ticketmaster best-effort)**")
    if not events:
        lines.append("• (no events found / TM module unavailable)")
    else:
        for ev in events[:8]:
            name = ev.get("name") or ev.get("title") or "Event"
            date = ev.get("date") or ev.get("localDate") or ev.get("start_date") or ""
            city = ev.get("city") or ev.get("venue_city") or ""
            venue = ev.get("venue") or ev.get("venue_name") or ""
            url = ev.get("url") or ""
            cap = ev.get("capacity") or ev.get("venue_capacity")

            city_score = 1.0
            if city and ranked:
                for rr in ranked:
                    if str(rr.get("city", "")).lower() == str(city).lower():
                        try:
                            city_score = float(rr.get("score") or 1.0)
                        except Exception:
                            city_score = 1.0
                        break

            sellout = _sellout_probability(stars, city_score, int(cap) if str(cap).isdigit() else None)

            line = f"• {name} — {date} {city}".strip()
            line += f" — sellout **{sellout}%**"
            lines.append(line)
            if venue:
                lines.append(f"  Venue: {venue}")
            if url:
                lines.append(f"  Tickets: {url}")

    lines.append("")
    lines.append("**On-sale dates**")
    lines.append("• Presale: (add enrichment next)")
    lines.append("• General sale: (add enrichment next)")

    return _safe_truncate("\n".join(lines).strip(), 1900)

def _tour_scan_post_callback(item: Dict[str, Any]) -> str:
    if TOUR_SCAN_MODE == "full":
        return _tour_full_intel_message(item)
    return _tour_fast_message(item)

def _start_tour_scan_monitor() -> None:
    if not tour_scan_monitor:
        logger.info("tour_scan_monitor not available.")
        return

    fn = getattr(tour_scan_monitor, "start_background_thread", None)
    if callable(fn):
        try:
            fn(
                interval_seconds=TOUR_SCAN_POLL_SECONDS,
                post_callback=_tour_scan_post_callback,
                discord_client=client,
                channel_id=TOUR_SCAN_ALERT_CHANNEL_ID or DEFAULT_CHANNEL_ID,
            )
            logger.info("tour_scan_monitor background thread started (module-managed).")
            return
        except TypeError:
            # legacy signature / dict signature
            try:
                fn(
                    {
                        "interval_seconds": TOUR_SCAN_POLL_SECONDS,
                        "post_callback": _tour_scan_post_callback,
                        "discord_client": client,
                        "channel_id": TOUR_SCAN_ALERT_CHANNEL_ID or DEFAULT_CHANNEL_ID,
                    }
                )
                logger.info("tour_scan_monitor background thread started (legacy signature).")
                return
            except Exception as e:
                logger.warning("tour_scan_monitor start_background_thread failed: %s", e)

    logger.info("tour_scan_monitor.start_background_thread missing.")

# ---------------------------------------------------------------------
# Intel v1.0 helpers
# ---------------------------------------------------------------------
def _stars_for_artist(artist: str) -> int:
    if tour_scan_monitor and hasattr(tour_scan_monitor, "rate_artist"):
        try:
            v = int(tour_scan_monitor.rate_artist(artist))
            return max(1, min(v, 5))
        except Exception:
            pass
    return 3  # reasonable default

def _stars_emoji(stars: int) -> str:
    stars = max(1, min(int(stars), 5))
    return "⭐" * stars

def _safe_get(d: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None

def _best_cities_for_artist(artist: str, limit: int = 25) -> List[Dict[str, Any]]:
    """
    Weighted ranking:
    - city_boosts.rank_cities() (base + TM density + RSS history)
    - fallback to tour_scan_monitor.top_cities_for_artist()
    """
    if city_boosts and hasattr(city_boosts, "rank_cities"):
        try:
            ranked = city_boosts.rank_cities(artist, max_results=limit)
            if isinstance(ranked, list) and ranked:
                return ranked
        except Exception:
            pass

    # fallback
    if tour_scan_monitor and hasattr(tour_scan_monitor, "top_cities_for_artist"):
        try:
            cities = list(tour_scan_monitor.top_cities_for_artist(artist))
            out = [{"city": c, "score": 1.0, "components": {"fallback": 1.0}} for c in cities[:limit]]
            return out
        except Exception:
            pass

    return []

def _sellout_probability(artist_stars: int, city_score: float, venue_capacity: Optional[int]) -> int:
    """
    Heuristic:
    - stars (1..5) drives baseline
    - city_score boosts based on demand signals
    - capacity reduces sellout likelihood at large venues
    """
    stars = max(1, min(int(artist_stars), 5))
    base = {1: 25, 2: 40, 3: 55, 4: 70, 5: 82}.get(stars, 55)

    # city_score ~ 1.0..(1+weights). normalize softly
    boost = min(18.0, max(0.0, (float(city_score) - 1.0) * 7.5))

    cap_penalty = 0.0
    if venue_capacity:
        try:
            cap = int(venue_capacity)
            if cap >= 20000:
                cap_penalty = 18
            elif cap >= 15000:
                cap_penalty = 12
            elif cap >= 10000:
                cap_penalty = 7
            elif cap <= 3500:
                cap_penalty = -8
        except Exception:
            pass

    val = base + boost - cap_penalty
    return int(max(0, min(100, round(val))))

def _tm_events_for_artist(artist: str, limit: int = 10) -> List[Dict[str, Any]]:
    if not ticketmaster_agent:
        return []
    fn = getattr(ticketmaster_agent, "search_events_for_artist", None)
    if not callable(fn):
        return []
    try:
        res = fn(artist, limit)
        return res if isinstance(res, list) else []
    except Exception:
        return []

async def _intel_v1(artist: str) -> str:
    artist = (artist or "").strip()
    if not artist:
        return "❌ Provide an artist name."

    # If you have a full engine, prefer it
    if tour_intel_agent and hasattr(tour_intel_agent, "build_artist_intel"):
        try:
            out = await asyncio.to_thread(tour_intel_agent.build_artist_intel, artist)
            return out if isinstance(out, str) else json.dumps(out, indent=2, default=str)
        except Exception:
            pass

    stars = _stars_for_artist(artist)
    ranked = _best_cities_for_artist(artist, limit=25)

    # Socials / metrics
    spotify_txt = "unavailable"
    youtube_txt = "unavailable"
    tiktok_txt = "unavailable"
    streaming_txt = "unavailable"
    socials_heat_txt = "unavailable"

    try:
        if spotify_agent and hasattr(spotify_agent, "get_spotify_profile"):
            sp = await asyncio.to_thread(spotify_agent.get_spotify_profile, artist, True)
            followers = _safe_get(sp, "followers", "followers_total")
            popularity = _safe_get(sp, "popularity")
            spotify_txt = f"followers={followers}, popularity={popularity}"
    except Exception:
        pass

    try:
        if youtube_agent and hasattr(youtube_agent, "get_youtube_profile"):
            yt = await asyncio.to_thread(youtube_agent.get_youtube_profile, artist, True)
            subs = _safe_get(yt, "subscribers", "subscriberCount")
            views = _safe_get(yt, "views", "viewCount")
            youtube_txt = f"subs={subs}, views={views}"
    except Exception:
        pass

    try:
        if tiktok_agent and hasattr(tiktok_agent, "get_tiktok_stats"):
            tk = await tiktok_agent.get_tiktok_stats(artist)
            followers = _safe_get(tk, "followers")
            likes = _safe_get(tk, "likes")
            tiktok_txt = f"followers={followers}, likes={likes}"
    except Exception:
        pass

    try:
        if streaming_metrics and hasattr(streaming_metrics, "get_spotify_metrics"):
            sm = await asyncio.to_thread(streaming_metrics.get_spotify_metrics, artist)
            monthly = _safe_get(sm, "monthly_listeners", "monthlyListeners")
            streaming_txt = f"monthly_listeners={monthly}"
    except Exception:
        pass

    try:
        if socials_agent and hasattr(socials_agent, "get_socials_heat"):
            heat = await socials_agent.get_socials_heat(artist)
            score = _safe_get(heat, "score", "heat_score")
            socials_heat_txt = f"heat_score={score}"
    except Exception:
        pass

    # Events (Ticketmaster)
    events = _tm_events_for_artist(artist, limit=12)

    # Build output
    lines: List[str] = []
    lines.append(f"**{artist} — Intel v1.0**")
    lines.append(f"Rating: {_stars_emoji(stars)} ({stars}/5)")
    lines.append("")
    lines.append("**Social stats (best-effort)**")
    lines.append(f"• Spotify: {spotify_txt}")
    lines.append(f"• YouTube: {youtube_txt}")
    lines.append(f"• TikTok: {tiktok_txt}")
    lines.append(f"• Streaming: {streaming_txt}")
    lines.append(f"• Socials heat: {socials_heat_txt}")

    lines.append("")
    lines.append("**Best demand cities (weighted)**")
    if ranked:
        top = ranked[:20]
        for r in top:
            lines.append(f"• {r.get('city')} (score={r.get('score')})")
    else:
        lines.append("• (no city ranking available)")

    lines.append("")
    lines.append("**Tour / Events (Ticketmaster best-effort)**")
    if not events:
        lines.append("• (no events found / TM module unavailable)")
    else:
        for ev in events[:10]:
            eid = ev.get("id") or ev.get("event_id") or "?"
            name = ev.get("name") or ev.get("title") or "Event"
            date = ev.get("date") or ev.get("localDate") or ev.get("start_date") or ""
            venue = ev.get("venue") or ev.get("venue_name") or ""
            city = ev.get("city") or ev.get("venue_city") or ""
            url = ev.get("url") or ""
            cap = ev.get("capacity") or ev.get("venue_capacity")

            # if city found in ranked list, use score
            city_score = 1.0
            if city:
                for rr in ranked[:30]:
                    if str(rr.get("city", "")).lower() == str(city).lower():
                        try:
                            city_score = float(rr.get("score") or 1.0)
                        except Exception:
                            city_score = 1.0
                        break

            sellout = _sellout_probability(stars, city_score, int(cap) if str(cap).isdigit() else None)

            lines.append(f"• `{eid}` — {name} ({date} {city}) — sellout: **{sellout}%**".strip())
            if venue:
                lines.append(f"  Venue: {venue}")
            if url:
                lines.append(f"  Tickets: {url}")

    lines.append("")
    lines.append("**Links (best-effort)**")
    lines.append("• Official site: (add via Tavily/Google agent enrichment next)")
    lines.append("• Presales: (add via Tavily/Google agent enrichment next)")

    return _safe_truncate("\n".join(lines), 1900)

# ---------------------------------------------------------------------
# TM surge watch startup (background loop)
# ---------------------------------------------------------------------
async def _start_tm_surge_watch() -> None:
    if tm_surge_watch is None:
        logger.info("tm_surge_watch not available; skipping.")
        return
    is_avail = getattr(tm_surge_watch, "is_available", None)
    if callable(is_avail) and not is_avail():
        logger.info("tm_surge_watch ticketmaster agent unavailable; skipping.")
        return

    async def _discord_post(msg: str) -> None:
        await _send_to_mixed_channel(msg, prefer="tour")
        STATUS["last_posts"]["tm_surge_unix"] = time.time()

    stop_event = asyncio.Event()  # reserved if you later want shutdown support
    task = asyncio.create_task(
        tm_surge_watch.surge_watch_loop(discord_post=_discord_post, stop_event=stop_event),
        name="tm_surge_watch",
    )
    _task_guard("tm_surge_watch", task)
    logger.info("TM surge watch loop started (%ss).", TM_SURGE_POLL_SECONDS)


async def _start_drop_catcher() -> None:
    if drop_catcher is None:
        logger.info("drop_catcher not available; skipping.")
        return
    is_avail = getattr(drop_catcher, "is_available", None)
    if callable(is_avail) and not is_avail():
        logger.info("drop_catcher: ticketmaster agent unavailable; skipping.")
        return

    async def _discord_post(payload) -> None:
        if isinstance(payload, dict):
            if _EMBED_BUILDER_OK:
                try:
                    embed, view = build_alert(payload, link_only=False)
                    role_id = os.getenv("DROP_PING_ROLE_ID", "")
                    content = f"<@&{role_id}>" if role_id else ""
                    await post_rich(embed, view, content=content, prefer="drop")
                except Exception as e:
                    logger.warning("drop_catcher embed build failed: %s", e)
                    await post_message(payload.get("text", str(payload)), prefer="drop")
            else:
                await post_message(payload.get("text", str(payload)), prefer="drop")
        else:
            await post_message(str(payload), prefer="drop")
        STATUS["last_posts"]["drop_catcher_unix"] = time.time()

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        drop_catcher.drop_watch_loop(discord_post=_discord_post, stop_event=stop_event),
        name="drop_catcher",
    )
    _task_guard("drop_catcher", task)
    logger.info("Drop catcher loop started (%ss).", DROP_CATCHER_POLL_SECONDS)


async def _start_platform_monitor() -> None:
    if platform_monitor is None:
        logger.info("platform_monitor not available; skipping.")
        return
    if not getattr(platform_monitor, "is_available", lambda: False)():
        logger.info("platform_monitor: requests or db unavailable; skipping.")
        return

    async def _platform_discord_post(payload) -> None:
        if isinstance(payload, dict):
            if _EMBED_BUILDER_OK:
                try:
                    embed, view = build_alert(payload, link_only=False)
                    role_id = os.getenv("DROP_PING_ROLE_ID", "")
                    content = f"<@&{role_id}>" if role_id else ""
                    await post_rich(embed, view, content=content, prefer="drop")
                except Exception as e:
                    logger.warning("platform_monitor embed build failed: %s", e)
                    await post_message(payload.get("text", str(payload)), prefer="drop")
            else:
                await post_message(payload.get("text", str(payload)), prefer="drop")
        else:
            await post_message(str(payload), prefer="drop")
        STATUS["last_posts"]["platform_monitor_unix"] = time.time()

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        platform_monitor.platform_watch_loop(
            discord_post=_platform_discord_post, stop_event=stop_event
        ),
        name="platform_monitor",
    )
    _task_guard("platform_monitor", task)
    logger.info("Platform monitor loop started.")


async def _daily_digest_loop() -> None:
    """
    Posts a morning digest of today's US/CA music on-sales once per day
    at DROP_DIGEST_HOUR_UTC (default 9 AM UTC).
    """
    if not DROP_DIGEST_ENABLED:
        logger.info("Daily digest disabled.")
        return
    if drop_catcher is None or not getattr(drop_catcher, "scan_tomorrow_onsales", None):
        logger.info("Daily digest: drop_catcher not available; skipping.")
        return

    logger.info("Daily digest loop started (fires at %02d:00 UTC).", DROP_DIGEST_HOUR_UTC)

    while True:
        now_utc = _dt.datetime.now(_dt.timezone.utc)
        # Seconds until the next target hour today (or tomorrow if already past)
        target = now_utc.replace(hour=DROP_DIGEST_HOUR_UTC, minute=0, second=0, microsecond=0)
        if now_utc >= target:
            target += _dt.timedelta(days=1)
        wait_secs = (target - now_utc).total_seconds()
        logger.info("Daily digest: next post in %.0f seconds.", wait_secs)
        await asyncio.sleep(wait_secs)

        try:
            result = await asyncio.to_thread(drop_catcher.scan_tomorrow_onsales)
            events = result.get("events") or []
            date_str = result.get("date") or "tomorrow"

            if not events:
                logger.info("Daily digest: no on-sales found for %s.", date_str)
            else:
                lines = [f"☀️ **Daily Drop Digest — on-sales for {date_str}** ({len(events)} events, US/CA)"]
                for ev in events[:20]:
                    name = ev.get("name") or "?"
                    artist = ev.get("artist") or ""
                    city = ev.get("city") or ""
                    show_date = ev.get("event_local_date") or ""
                    pmin = ev.get("price_min")
                    pmax = ev.get("price_max")
                    url = ev.get("url") or ""
                    line = f"• **{name}**"
                    if artist:
                        line += f" — {artist}"
                    if city:
                        line += f" @ {city}"
                    if show_date:
                        line += f" ({show_date})"
                    if pmin is not None and pmax is not None:
                        line += f" `${pmin:.0f}–${pmax:.0f}`"
                    lines.append(line)
                    if url:
                        lines.append(f"  {url}")

                msg = _safe_truncate("\n".join(lines), 1900)
                await post_message(msg, prefer="drop")
                STATUS["last_posts"]["drop_catcher_unix"] = time.time()
                logger.info("Daily digest posted: %d events for %s.", len(events), date_str)
        except Exception as e:
            STATUS["last_error"] = f"daily_digest: {e}"
            logger.warning("Daily digest error: %s", e)

        # Sleep 60s after posting to avoid double-firing on the same hour
        await asyncio.sleep(60)


# ---------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------

@tree.command(name="tour_scan_now", description="Run tour scan immediately (RSS)")
async def tour_scan_now_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        from tour_scan_monitor import run_once
    except Exception as e:
        await interaction.followup.send(f"❌ tour_scan_monitor not available: {e}", ephemeral=True)
        return

    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(None, run_once)

    await interaction.followup.send(
        f"✅ Tour scan completed: feeds={stats.get('feeds',0)} "
        f"fetched={stats.get('fetched_items',0)} new={stats.get('new_items',0)} "
        f"errors={stats.get('errors',0)}",
        ephemeral=True
    )

@tree.command(name="help", description="Show all Viking AI slash commands (auto-documented).")
async def help_cmd(interaction: discord.Interaction) -> None:
    cmds = sorted(tree.get_commands(), key=lambda c: c.name)
    lines = [
        "**Viking AI — Slash Commands**",
        "",
        "Access: Everyone (no tiers / no admin gating)",
        "",
    ]
    for c in cmds:
        desc = getattr(c, "description", "") or ""
        lines.append(f"• `/{c.name}` — {desc}".strip())
    await _send_ephemeral(interaction, "\n".join(lines))

@tree.command(name="status", description="Status: uptime, memory, monitors + last posts.")
async def status_cmd(interaction: discord.Interaction) -> None:
    data = {
        "rev": _git_rev(),
        "uptime_seconds": _uptime_seconds(),
        "memory_rss_mb": _rss_mb(),
        "monitors": STATUS["monitors"],
        "intervals_seconds": {
            "price_monitor": PRICE_POLL_SECONDS,
            "verified_fan_monitor": VERIFIED_FAN_POLL_SECONDS,
            "tour_scan_monitor": TOUR_SCAN_POLL_SECONDS,
            "tm_surge_poll": TM_SURGE_POLL_SECONDS,
            "drop_catcher": DROP_CATCHER_POLL_SECONDS,
            "drop_digest_hour_utc": DROP_DIGEST_HOUR_UTC,
            "intel_refresh": INTEL_REFRESH_SECONDS,
        },
        "tour_scan_mode": TOUR_SCAN_MODE,
        "last_posts": STATUS["last_posts"],
        "last_error": STATUS.get("last_error"),
        "pid": os.getpid(),
    }
    await _send_ephemeral(interaction, f"```json\n{json.dumps(data, indent=2, default=str)}\n```")

@tree.command(name="health", description="Health JSON dump (ephemeral).")
async def health_cmd(interaction: discord.Interaction) -> None:
    data = {
        "ok": True,
        "rev": _git_rev(),
        "uptime_seconds": _uptime_seconds(),
        "memory_rss_mb": _rss_mb(),
        "pid": os.getpid(),
        "sync": STATUS["sync"],
        "monitors": STATUS["monitors"],
        "last_posts": STATUS["last_posts"],
        "last_error": STATUS.get("last_error"),
    }
    await _send_ephemeral(interaction, f"```json\n{json.dumps(data, indent=2, default=str)}\n```")

@tree.command(name="debug", description="Debug dump (safe/truncated).")
async def debug_cmd(interaction: discord.Interaction) -> None:
    data = {
        "env": {
            "GUILD_ID": GUILD_ID,
            "DEFAULT_CHANNEL_ID": DEFAULT_CHANNEL_ID,
            "PRICE_ALERT_CHANNEL_ID": PRICE_ALERT_CHANNEL_ID,
            "VERIFIED_FAN_ALERT_CHANNEL_ID": VERIFIED_FAN_ALERT_CHANNEL_ID,
            "TOUR_SCAN_ALERT_CHANNEL_ID": TOUR_SCAN_ALERT_CHANNEL_ID,
            "PRICE_WEBHOOK_URL_set": bool(PRICE_WEBHOOK_URL),
            "VERIFIED_FAN_WEBHOOK_URL_set": bool(VERIFIED_FAN_WEBHOOK_URL),
            "TOUR_SCAN_WEBHOOK_URL_set": bool(TOUR_SCAN_WEBHOOK_URL),
            "TOUR_SCAN_MODE": TOUR_SCAN_MODE,
        },
        "sync": STATUS["sync"],
        "tasks": sorted(TASKS.keys()),
    }
    await _send_ephemeral(interaction, f"```json\n{_safe_truncate(json.dumps(data, indent=2, default=str), 1800)}\n```")

@tree.command(name="diag", description="Diagnostics: uptime/memory/app/guild IDs + sync state + registered commands.")
async def diag_cmd(interaction: discord.Interaction) -> None:
    app_id = getattr(client.user, "id", None)
    data = {
        "rev": _git_rev(),
        "uptime_seconds": _uptime_seconds(),
        "memory_mb": _rss_mb(),
        "application_id": str(app_id) if app_id else "unknown",
        "guild_id": str(GUILD_ID) if GUILD_ID else "0",
        "sync_target": STATUS["sync"].get("target"),
        "registered_commands": ", ".join(sorted([c.name for c in tree.get_commands()])),
        "last_sync_unix": STATUS["sync"].get("last_sync_unix"),
        "last_sync_count": STATUS["sync"].get("last_sync_count"),
        "last_sync_ok": STATUS["sync"].get("last_sync_ok"),
        "last_sync_error": STATUS["sync"].get("last_sync_error") or "none",
    }
    await _send_ephemeral(interaction, "\n".join([f"{k}: {v}" for k, v in data.items()]))

@tree.command(name="sync_now", description="Force re-sync slash commands (guild if GUILD_ID set, else global).")
async def sync_now_cmd(interaction: discord.Interaction) -> None:
    ok, msg = await sync_slash_commands(reason="manual:/sync_now")
    payload = {
        "ok": ok,
        "target": STATUS["sync"]["target"],
        "count": STATUS["sync"]["last_sync_count"],
        "registered": sorted([c.name for c in tree.get_commands()]),
        "changed": None,
        "detail": msg,
    }
    await _send_ephemeral(interaction, f"```json\n{json.dumps(payload, indent=2)}\n```")

@tree.command(name="news_now", description="Get latest tour/news intel (best-effort).")
@app_commands.describe(artist="Optional artist filter")
async def news_now_cmd(interaction: discord.Interaction, artist: Optional[str] = None) -> None:
    await interaction.response.defer(thinking=True)
    try:
        if tour_news_agent_v3 and hasattr(tour_news_agent_v3, "get_tour_news"):
            news = await asyncio.to_thread(tour_news_agent_v3.get_tour_news, artist or "")
            txt = news if isinstance(news, str) else json.dumps(news, indent=2, default=str)
            await interaction.followup.send(_safe_truncate(txt, 1900), ephemeral=True)
            return
        await interaction.followup.send("❌ News engine not available in this build.", ephemeral=True)
    except Exception as e:
        STATUS["last_error"] = f"news_now: {e}"
        await interaction.followup.send(f"❌ news_now failed: {e}", ephemeral=True)

@tree.command(name="intel", description="Intel v1.0: stars + socials + best cities + sellout probabilities + links (best-effort).")
@app_commands.describe(artist="Artist name")
async def intel_cmd(interaction: discord.Interaction, artist: str) -> None:
    await interaction.response.defer(thinking=True)
    try:
        txt = await _intel_v1(artist)
        await interaction.followup.send(_safe_truncate(txt, 1900), ephemeral=True)
    except Exception as e:
        STATUS["last_error"] = f"intel: {e}"
        await interaction.followup.send(f"❌ intel failed: {e}", ephemeral=True)

@tree.command(name="intel_refresh", description="Refresh intel caches (best-effort).")
async def intel_refresh_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)
    try:
        # best-effort cache refresh hook if you add it later
        if tour_intel_agent and hasattr(tour_intel_agent, "refresh_intel_caches"):
            await asyncio.to_thread(tour_intel_agent.refresh_intel_caches)
        await interaction.followup.send("✅ Intel refresh done (best-effort).", ephemeral=True)
    except Exception as e:
        STATUS["last_error"] = f"intel_refresh: {e}"
        await interaction.followup.send(f"❌ intel_refresh failed: {e}", ephemeral=True)

@tree.command(name="events", description="Search Ticketmaster events by artist name.")
@app_commands.describe(artist="Artist name", limit="Max results (default 10)")
async def events_cmd(interaction: discord.Interaction, artist: str, limit: Optional[int] = 10) -> None:
    await interaction.response.defer(thinking=True)
    if not ticketmaster_agent or not hasattr(ticketmaster_agent, "search_events_for_artist"):
        await interaction.followup.send("❌ Ticketmaster search not available in this build.", ephemeral=True)
        return
    try:
        limit_int = max(1, min(int(limit or 10), 25))
        res = await asyncio.to_thread(ticketmaster_agent.search_events_for_artist, artist, limit_int)
        if not res:
            await interaction.followup.send("No events found.", ephemeral=True)
            return
        lines = [f"**Ticketmaster events for _{artist}_**"]
        for ev in res[:limit_int]:
            name = ev.get("name") or ev.get("title") or "Event"
            eid = ev.get("id") or ev.get("event_id") or ""
            date = ev.get("date") or ev.get("localDate") or ev.get("start_date") or ""
            city = ev.get("city") or ev.get("venue_city") or ""
            url = ev.get("url") or ""
            line = f"• `{eid}` — **{name}**"
            if date or city:
                line += f" ({date} {city})".strip()
            if url:
                line += f"\n  {url}"
            lines.append(line)
        await interaction.followup.send(_safe_truncate("\n".join(lines), 1900), ephemeral=True)
    except Exception as e:
        STATUS["last_error"] = f"events: {e}"
        await interaction.followup.send(f"❌ events failed: {e}", ephemeral=True)

@tree.command(name="eventdetails", description="Get details for a Ticketmaster event id.")
@app_commands.describe(event_id="Ticketmaster event id")
async def eventdetails_cmd(interaction: discord.Interaction, event_id: str) -> None:
    await interaction.response.defer(thinking=True)
    if not ticketmaster_agent:
        await interaction.followup.send("❌ Ticketmaster not available in this build.", ephemeral=True)
        return
    getter = getattr(ticketmaster_agent, "get_event_details", None) or getattr(ticketmaster_agent, "event_details", None)
    if not callable(getter):
        await interaction.followup.send("❌ Ticketmaster event details not available in this build.", ephemeral=True)
        return
    try:
        ev = await asyncio.to_thread(getter, event_id)
        txt = json.dumps(ev, indent=2, default=str)
        await interaction.followup.send(f"```json\n{_safe_truncate(txt, 1900)}\n```", ephemeral=True)
    except Exception as e:
        STATUS["last_error"] = f"eventdetails: {e}"
        await interaction.followup.send(f"❌ eventdetails failed: {e}", ephemeral=True)

@tree.command(name="city_debug", description="Explain weighted city ranking components for an artist.")
@app_commands.describe(artist="Artist name")
async def city_debug_cmd(interaction: discord.Interaction, artist: str) -> None:
    ranked = _best_cities_for_artist(artist, limit=25)
    if not ranked:
        await _send_ephemeral(interaction, "No city ranking available.")
        return
    lines = [f"**City ranking debug — {artist}**", ""]
    for r in ranked[:20]:
        city = r.get("city")
        score = r.get("score")
        comp = r.get("components") or {}
        lines.append(f"• {city} score={score} components={comp}")
    await _send_ephemeral(interaction, _safe_truncate("\n".join(lines), 1900))

# ---------------------------------------------------------------------
# Drop catcher slash commands
# ---------------------------------------------------------------------

@tree.command(name="drop_add", description="Watch a Ticketmaster event ID for ticket availability drops.")
@app_commands.describe(event_id="Ticketmaster event ID", artist="Artist name (optional label)", days="Days to watch (default 7)")
async def drop_add_cmd(interaction: discord.Interaction, event_id: str, artist: Optional[str] = "", days: Optional[int] = 7) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    if not await _tier_gate(interaction, "PRO"):
        return
    if drop_catcher is None or not getattr(drop_catcher, "add_drop_watch", None):
        await interaction.followup.send("❌ Drop catcher not available in this build.", ephemeral=True)
        return
    try:
        ok, msg = await asyncio.to_thread(drop_catcher.add_drop_watch, event_id, artist or "", int(days or 7))
        await interaction.followup.send(("✅ " if ok else "❌ ") + msg, ephemeral=True)
    except Exception as e:
        STATUS["last_error"] = f"drop_add: {e}"
        await interaction.followup.send(f"❌ drop_add failed: {e}", ephemeral=True)


@tree.command(name="drop_list", description="List all active drop watch events.")
async def drop_list_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    if drop_catcher is None or not getattr(drop_catcher, "list_drop_watches", None):
        await interaction.followup.send("❌ Drop catcher not available in this build.", ephemeral=True)
        return
    try:
        rows = await asyncio.to_thread(drop_catcher.list_drop_watches)
        if not rows:
            await interaction.followup.send("No drop watches active.", ephemeral=True)
            return
        now = time.time()
        lines = ["**Active drop watches**"]
        for r in rows[:25]:
            eid = r.get("event_id") or "?"
            name = r.get("name") or r.get("artist") or eid
            expires = float(r.get("expires_at_unix") or 0)
            hrs = max(0, int((expires - now) / 3600))
            status = r.get("last_status") or "unknown"
            checked = r.get("last_check_unix") or 0
            checked_ago = int((now - checked) / 60) if checked else None
            checked_str = f"{checked_ago}m ago" if checked_ago is not None else "never"
            lines.append(f"• `{eid}` — {name} | status=`{status}` | last checked {checked_str} | expires ~{hrs}h")
        await interaction.followup.send(_safe_truncate("\n".join(lines), 1900), ephemeral=True)
    except Exception as e:
        STATUS["last_error"] = f"drop_list: {e}"
        await interaction.followup.send(f"❌ drop_list failed: {e}", ephemeral=True)


@tree.command(name="drop_remove", description="Remove a Ticketmaster event from the drop watch list.")
@app_commands.describe(event_id="Ticketmaster event ID to stop watching")
async def drop_remove_cmd(interaction: discord.Interaction, event_id: str) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    if not await _tier_gate(interaction, "PRO"):
        return
    if drop_catcher is None or not getattr(drop_catcher, "remove_drop_watch", None):
        await interaction.followup.send("❌ Drop catcher not available in this build.", ephemeral=True)
        return
    try:
        ok, msg = await asyncio.to_thread(drop_catcher.remove_drop_watch, event_id)
        await interaction.followup.send(("✅ " if ok else "❌ ") + msg, ephemeral=True)
    except Exception as e:
        STATUS["last_error"] = f"drop_remove: {e}"
        await interaction.followup.send(f"❌ drop_remove failed: {e}", ephemeral=True)


@tree.command(name="drop_aggressive", description="Manually register an event for aggressive 3s polling before its on-sale time.")
@app_commands.describe(
    event_id="TM event ID to target",
    onsale_in_minutes="Minutes until on-sale starts (e.g. 10 means T-10min from now)",
    event_url="Direct link to the event (optional, used for pre-warming)",
)
async def drop_aggressive_cmd(
    interaction: discord.Interaction,
    event_id: str,
    onsale_in_minutes: float,
    event_url: Optional[str] = "",
) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    if not drop_catcher or not hasattr(drop_catcher, "register_aggressive_target"):
        await interaction.followup.send("❌ drop_catcher not available.", ephemeral=True)
        return
    if onsale_in_minutes <= 0:
        await interaction.followup.send("❌ onsale_in_minutes must be > 0.", ephemeral=True)
        return
    try:
        onsale_unix = time.time() + onsale_in_minutes * 60
        drop_catcher.register_aggressive_target(event_id.strip(), onsale_unix, url=event_url or "")
        await interaction.followup.send(
            f"✅ Aggressive mode armed for `{event_id}` — on-sale in **{onsale_in_minutes:.0f} min**.\n"
            f"Polling every {drop_catcher.AGGRESSIVE_POLL_SECONDS}s. Pre-warm fires at T-60s.",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(f"❌ drop_aggressive failed: {e}", ephemeral=True)


@tree.command(name="drop_search", description="Search Ticketmaster events by artist then watch one for drops.")
@app_commands.describe(artist="Artist name to search", limit="Max results (default 8)")
async def drop_search_cmd(interaction: discord.Interaction, artist: str, limit: Optional[int] = 8) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    if not await _tier_gate(interaction, "PRO"):
        return
    if not ticketmaster_agent or not hasattr(ticketmaster_agent, "search_events_for_artist"):
        await interaction.followup.send("❌ Ticketmaster not available in this build.", ephemeral=True)
        return
    try:
        limit_int = max(1, min(int(limit or 8), 15))
        events = await asyncio.to_thread(ticketmaster_agent.search_events_for_artist, artist, limit_int)
        if not events:
            await interaction.followup.send(f"No events found for **{artist}**.", ephemeral=True)
            return

        lines = [f"**Events for _{artist}_** — use `/drop_add <event_id>` to watch one:"]
        for ev in events[:limit_int]:
            norm = drop_catcher._normalize_event(ev) if drop_catcher else {}
            eid = norm.get("id") or "?"
            name = norm.get("name") or "Event"
            date = norm.get("event_local_date") or ""
            city = norm.get("city") or ""
            status = norm.get("status") or "unknown"
            pmin, pmax = norm.get("price_min"), norm.get("price_max")
            price_str = f" `${pmin:.0f}–${pmax:.0f}`" if pmin is not None and pmax is not None else ""
            line = f"• `{eid}` — **{name}**"
            if date or city:
                line += f" ({date} {city})".strip()
            line += f" status=`{status}`{price_str}"
            lines.append(line)

        lines.append("")
        lines.append("Use `/drop_add <event_id>` to start watching an event for drops.")
        await interaction.followup.send(_safe_truncate("\n".join(lines), 1900), ephemeral=True)
    except Exception as e:
        STATUS["last_error"] = f"drop_search: {e}"
        await interaction.followup.send(f"❌ drop_search failed: {e}", ephemeral=True)


@tree.command(name="drop_changes", description="Scan Ticketmaster now for music events that just went on-sale (US/CA).")
@app_commands.describe(hours="Look-back window in hours (default 1)")
async def drop_changes_cmd(interaction: discord.Interaction, hours: Optional[int] = 1) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    if not await _tier_gate(interaction, "PRO"):
        return
    if drop_catcher is None or not getattr(drop_catcher, "scan_new_onsales", None):
        await interaction.followup.send("❌ Drop catcher global scan not available in this build.", ephemeral=True)
        return
    try:
        hours_int = max(1, min(int(hours or 1), 24))
        result = await asyncio.to_thread(drop_catcher.scan_new_onsales, hours_int)
        new_items = result.get("new") or []
        updated_items = result.get("updated") or []
        total = len(new_items) + len(updated_items)
        if not total:
            await interaction.followup.send(
                f"No new or updated on-sales in last {hours_int}h (window: {result.get('since')} → {result.get('until')}).",
                ephemeral=True,
            )
            return
        lines = [f"**On-sale scan — last {hours_int}h** ({total} found)"]
        for ev in (new_items + updated_items)[:20]:
            name = ev.get("name") or "?"
            artist = ev.get("artist") or ""
            city = ev.get("city") or ""
            date_str = ev.get("event_local_date") or ""
            pmin = ev.get("price_min")
            pmax = ev.get("price_max")
            change = ev.get("change_type") or ""
            tag = "🆕" if change == "NEW" else "🔄"
            line = f"{tag} **{name}**"
            if artist:
                line += f" — {artist}"
            if city or date_str:
                line += f" ({city} {date_str})".strip()
            if pmin is not None and pmax is not None:
                line += f" `${pmin:.0f}–${pmax:.0f}`"
            lines.append(line)
        await interaction.followup.send(_safe_truncate("\n".join(lines), 1900), ephemeral=True)
    except Exception as e:
        STATUS["last_error"] = f"drop_changes: {e}"
        await interaction.followup.send(f"❌ drop_changes failed: {e}", ephemeral=True)


@tree.command(name="drop_tomorrow", description="Preview US/CA music events going on-sale tomorrow.")
async def drop_tomorrow_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    if drop_catcher is None or not getattr(drop_catcher, "scan_tomorrow_onsales", None):
        await interaction.followup.send("❌ Drop catcher global scan not available in this build.", ephemeral=True)
        return
    try:
        result = await asyncio.to_thread(drop_catcher.scan_tomorrow_onsales)
        events = result.get("events") or []
        date_str = result.get("date") or "tomorrow"
        if not events:
            await interaction.followup.send(f"No music on-sales found for {date_str} (US/CA).", ephemeral=True)
            return
        lines = [f"**Music on-sales for {date_str}** ({len(events)} events, US/CA)"]
        for ev in events[:20]:
            name = ev.get("name") or "?"
            artist = ev.get("artist") or ""
            city = ev.get("city") or ""
            show_date = ev.get("event_local_date") or ""
            pmin = ev.get("price_min")
            pmax = ev.get("price_max")
            url = ev.get("url") or ""
            line = f"• **{name}**"
            if artist:
                line += f" — {artist}"
            if city:
                line += f" @ {city}"
            if show_date:
                line += f" ({show_date})"
            if pmin is not None and pmax is not None:
                line += f" `${pmin:.0f}–${pmax:.0f}`"
            lines.append(line)
            if url:
                lines.append(f"  {url}")
        await interaction.followup.send(_safe_truncate("\n".join(lines), 1900), ephemeral=True)
    except Exception as e:
        STATUS["last_error"] = f"drop_tomorrow: {e}"
        await interaction.followup.send(f"❌ drop_tomorrow failed: {e}", ephemeral=True)


# ---------------------------------------------------------------------
# Surge watch slash commands
# ---------------------------------------------------------------------
@tree.command(name="surge_add", description="Enable Ticketmaster surge watch for an artist.")
@app_commands.describe(artist="Artist name", days="Number of days to watch (default 5)")
async def surge_add_cmd(interaction: discord.Interaction, artist: str, days: Optional[int] = 5) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    if tm_surge_watch is None or not getattr(tm_surge_watch, "add_surge_artist", None):
        await interaction.followup.send("❌ Surge watch not available in this build.", ephemeral=True)
        return
    try:
        ok, msg = await asyncio.to_thread(tm_surge_watch.add_surge_artist, artist, int(days or 5))
        await interaction.followup.send(("✅ " if ok else "❌ ") + msg.replace("✅ ", "").replace("❌ ", ""), ephemeral=True)
    except Exception as e:
        STATUS["last_error"] = f"surge_add: {e}"
        await interaction.followup.send(f"❌ surge_add failed: {e}", ephemeral=True)

@tree.command(name="surge_list", description="List active Ticketmaster surge watch artists.")
async def surge_list_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    if tm_surge_watch is None or not getattr(tm_surge_watch, "list_surge_artists", None):
        await interaction.followup.send("❌ Surge watch not available in this build.", ephemeral=True)
        return
    try:
        rows = await asyncio.to_thread(tm_surge_watch.list_surge_artists)
        if not rows:
            await interaction.followup.send("No surge artists enabled.", ephemeral=True)
            return
        lines = ["**Active surge artists**"]
        now = time.time()
        for r in rows[:25]:
            artist = r.get("artist")
            expires = float(r.get("expires_at_unix") or 0)
            hrs = max(0, int((expires - now) / 3600))
            lines.append(f"• {artist} — expires in ~{hrs}h")
        await interaction.followup.send(_safe_truncate("\n".join(lines), 1900), ephemeral=True)
    except Exception as e:
        STATUS["last_error"] = f"surge_list: {e}"
        await interaction.followup.send(f"❌ surge_list failed: {e}", ephemeral=True)

@tree.command(name="surge_remove", description="Disable Ticketmaster surge watch for an artist.")
@app_commands.describe(artist="Artist name")
async def surge_remove_cmd(interaction: discord.Interaction, artist: str) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    if tm_surge_watch is None or not getattr(tm_surge_watch, "remove_surge_artist", None):
        await interaction.followup.send("❌ Surge watch not available in this build.", ephemeral=True)
        return
    try:
        ok, msg = await asyncio.to_thread(tm_surge_watch.remove_surge_artist, artist)
        await interaction.followup.send(("✅ " if ok else "❌ ") + msg.replace("✅ ", "").replace("❌ ", ""), ephemeral=True)
    except Exception as e:
        STATUS["last_error"] = f"surge_remove: {e}"
        await interaction.followup.send(f"❌ surge_remove failed: {e}", ephemeral=True)

# ---------------------------------------------------------------------
# Tier management (admin only)
# ---------------------------------------------------------------------

@tree.command(name="tier_set", description="(Admin) Set this server's tier: FREE | PRO | ELITE.")
@app_commands.describe(tier="Tier to assign: FREE, PRO, or ELITE")
@app_commands.default_member_permissions(administrator=True)
async def tier_set_cmd(interaction: discord.Interaction, tier: str) -> None:
    await interaction.response.defer(ephemeral=True)
    tier = tier.strip().upper()
    if tier not in _TIER_ORDER:
        await _send_ephemeral(interaction, f"❌ Invalid tier `{tier}`. Use: FREE, PRO, ELITE.")
        return
    if not usage_db:
        await _send_ephemeral(interaction, "❌ usage_db not available in this build.")
        return
    try:
        await asyncio.to_thread(usage_db.set_guild_tier, str(interaction.guild_id), tier)
        await _send_ephemeral(interaction, f"✅ Guild tier set to **{tier}**.")
    except Exception as e:
        STATUS["last_error"] = f"tier_set: {e}"
        await _send_ephemeral(interaction, f"❌ tier_set failed: {e}")


@tree.command(name="tier_check", description="Show this server's current tier.")
async def tier_check_cmd(interaction: discord.Interaction) -> None:
    tier = _get_guild_tier(interaction.guild_id)
    await _send_ephemeral(interaction, f"This server is on tier: **{tier}**")


# ---------------------------------------------------------------------
# Subscription expiry DM warnings
# ---------------------------------------------------------------------

async def _expiry_warning_loop() -> None:
    """
    Runs every 12 hours.
    Sends a Discord DM to users whose subscription expires in exactly 7 or 3 days.
    Requires dashboard db to be reachable and client discord_id to be set.
    """
    await client.wait_until_ready()
    try:
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "dashboard"))
        import db as _dashboard_db
    except Exception as _e:
        logger.warning("expiry_warning_loop: cannot import dashboard db: %s", _e)
        return

    CHECK_INTERVAL = 12 * 3600  # 12 hours

    while not client.is_closed():
        try:
            for days_left in (7, 3):
                clients = _dashboard_db.get_clients_expiring_in(days_left)
                for c in clients:
                    discord_id = c.get("discord_id")
                    if not discord_id:
                        continue
                    try:
                        user_obj = await client.fetch_user(int(discord_id))
                        dm = await user_obj.create_dm()
                        tier = (c.get("tier") or "tester").title()
                        await dm.send(
                            f"⚠️ **Viking AI — Subscription Expiry Warning**\n\n"
                            f"Your **{tier}** plan expires in **{days_left} day{'s' if days_left != 1 else ''}**.\n\n"
                            f"To renew, contact the admin or redeem a code at your dashboard.\n"
                            f"Access will be locked immediately on expiry — no grace period."
                        )
                        logger.info(
                            "expiry_warning_loop: sent %dd warning to discord_id=%s", days_left, discord_id
                        )
                    except Exception as dm_err:
                        logger.debug(
                            "expiry_warning_loop: DM failed for discord_id=%s: %s", discord_id, dm_err
                        )
        except Exception as loop_err:
            logger.warning("expiry_warning_loop: error: %s", loop_err)

        await asyncio.sleep(CHECK_INTERVAL)


# ---------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------
@client.event
async def on_ready() -> None:
    logger.info("Starting Viking AI…")
    await sync_slash_commands(reason="startup")
    logger.info("Logged in as %s", client.user)

    # Tour scan (thread managed by module if available)
    _start_tour_scan_monitor()

    # Start surge watch loop (async)
    await _start_tm_surge_watch()

    # Start drop catcher loop (async)
    await _start_drop_catcher()

    # Start multi-platform monitor loop (async)
    await _start_platform_monitor()

    # Daily drop digest loop
    t_digest = asyncio.create_task(_daily_digest_loop(), name="daily_digest")
    _task_guard("daily_digest", t_digest)

    # Async monitor loops
    t_vf = asyncio.create_task(_verified_fan_loop(), name="verified_fan_loop")
    _task_guard("verified_fan_loop", t_vf)

    t_price = asyncio.create_task(_price_monitor_loop(), name="price_monitor_loop")
    _task_guard("price_monitor_loop", t_price)

    # Subscription expiry DM warnings (7d + 3d)
    t_expiry = asyncio.create_task(_expiry_warning_loop(), name="expiry_warning_loop")
    _task_guard("expiry_warning_loop", t_expiry)

def main() -> None:
    client.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main()
