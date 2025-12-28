import os
import asyncio
import json
import logging
import subprocess
import time
from typing import Any, Dict, Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

# --------------------
# Load env (absolute path so systemd WorkingDirectory doesn't matter)
# --------------------
load_dotenv("/opt/viking-ai/.env", override=False)

# --------------------
# Logging
# --------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("viking_ai")

# --------------------
# Optional imports (guarded)
# --------------------
try:
    import price_monitor
except Exception as e:
    price_monitor = None
    logger.warning("price_monitor import failed: %s", e)

try:
    import verified_fan_monitor
except Exception as e:
    verified_fan_monitor = None
    logger.warning("verified_fan_monitor import failed: %s", e)

try:
    import tour_scan_monitor
except Exception as e:
    tour_scan_monitor = None
    logger.warning("tour_scan_monitor import failed: %s", e)

try:
    from ticketmaster_agent_v2 import search_events_for_artist, get_event_details
except Exception:
    search_events_for_artist = None
    get_event_details = None

try:
    from agents.tour_news_agent_v3 import get_tour_news
except Exception:
    get_tour_news = None

# --------------------
# Config
# --------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

PRICE_ALERT_CHANNEL_ID = int(os.getenv("PRICE_ALERT_CHANNEL_ID", "0") or "0")
VERIFIED_FAN_ALERT_CHANNEL_ID = int(os.getenv("VERIFIED_FAN_ALERT_CHANNEL_ID", "0") or "0")
TOUR_SCAN_ALERT_CHANNEL_ID = int(os.getenv("TOUR_SCAN_ALERT_CHANNEL_ID", "0") or "0")

VERIFIED_FAN_WEBHOOK_URL = os.getenv("VERIFIED_FAN_WEBHOOK_URL", "").strip()
TOUR_SCAN_WEBHOOK_URL = os.getenv("TOUR_SCAN_WEBHOOK_URL", "").strip()

VERIFIED_FAN_POLL_SECONDS = int(os.getenv("VERIFIED_FAN_POLL_SECONDS", "7200") or "7200")

# Prefixes (for a mixed channel)
PRICE_PREFIX = (os.getenv("PRICE_PREFIX") or "[PRICE]").strip()
VERIFIED_FAN_PREFIX = (os.getenv("VERIFIED_FAN_PREFIX") or "[VF]").strip()
TOUR_SCAN_PREFIX = (os.getenv("TOUR_SCAN_PREFIX") or "[TOUR]").strip()

# Health/Watchdog
HEALTH_PING_SECONDS = int(os.getenv("HEALTH_PING_SECONDS", "60") or "60")
STARTUP_NOTIFY = (os.getenv("STARTUP_NOTIFY", "1") or "1").strip().lower() not in ("0", "false", "no")

START_TS = time.time()
STATUS: Dict[str, Any] = {
    "started_at_unix": START_TS,
    "last_price_post_unix": None,
    "last_vf_post_unix": None,
    "last_tour_post_unix": None,
    "last_error": None,
}

def _uptime_seconds() -> int:
    return int(time.time() - START_TS)

def _memory_mb() -> float:
    # Works on Linux without extra deps
    try:
        with open("/proc/self/statm", "r") as f:
            pages = int(f.read().split()[0])
        page_size = os.sysconf("SC_PAGE_SIZE")
        return (pages * page_size) / (1024 * 1024)
    except Exception:
        return -1.0

def _git_rev() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd="/opt/viking-ai",
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"

def _redact(s: str, keep: int = 6) -> str:
    if not s:
        return ""
    if len(s) <= keep:
        return "…" * len(s)
    return s[:keep] + "…" * 6

# --------------------
# Discord client
# --------------------
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# Keep references to background tasks to prevent GC
_bg_tasks: Dict[str, asyncio.Task] = {}

async def _get_channel(channel_id: int) -> Optional[discord.abc.Messageable]:
    if not channel_id:
        return None
    ch = client.get_channel(channel_id)
    if ch is None:
        try:
            ch = await client.fetch_channel(channel_id)
        except Exception:
            return None
    return ch

async def _send_webhook(webhook_url: str, content: str, embeds: Optional[list] = None) -> None:
    if not webhook_url:
        return
    try:
        wh = discord.SyncWebhook.from_url(webhook_url)
        await asyncio.to_thread(wh.send, content=content, embeds=embeds or None, wait=False)
    except Exception as e:
        logger.warning("Webhook send failed: %s", e)

async def _send_to_mixed_channel(content: str, prefer: str = "tour") -> None:
    """
    Prefer sending to an existing configured webhook (TOUR/VF) because those work even if the bot lacks send perms.
    Fallback to a Discord channel send.
    """
    if prefer == "tour" and TOUR_SCAN_WEBHOOK_URL:
        await _send_webhook(TOUR_SCAN_WEBHOOK_URL, content)
        return
    if prefer == "vf" and VERIFIED_FAN_WEBHOOK_URL:
        await _send_webhook(VERIFIED_FAN_WEBHOOK_URL, content)
        return
    if TOUR_SCAN_WEBHOOK_URL:
        await _send_webhook(TOUR_SCAN_WEBHOOK_URL, content)
        return
    if VERIFIED_FAN_WEBHOOK_URL:
        await _send_webhook(VERIFIED_FAN_WEBHOOK_URL, content)
        return

    ch = await _get_channel(PRICE_ALERT_CHANNEL_ID or VERIFIED_FAN_ALERT_CHANNEL_ID or TOUR_SCAN_ALERT_CHANNEL_ID)
    if ch:
        try:
            await ch.send(content)
        except Exception as e:
            logger.warning("Channel send failed: %s", e)

def _task_guard(name: str, task: asyncio.Task) -> None:
    def _done(t: asyncio.Task) -> None:
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        except Exception as e:
            STATUS["last_error"] = f"{name}: {e}"
            return
        if exc:
            STATUS["last_error"] = f"{name}: {exc}"
            logger.exception("Background task crashed: %s", name, exc_info=exc)
            asyncio.create_task(_send_to_mixed_channel(f"⚠️ {name} crashed: `{exc}`", prefer="tour"))

    task.add_done_callback(_done)
    _bg_tasks[name] = task

# --------------------
# Posting helpers (PRICE / VF)
# --------------------
async def post_price_alert(item: Dict[str, Any]) -> None:
    STATUS["last_price_post_unix"] = time.time()
    prefix = PRICE_PREFIX or "[PRICE]"
    try:
        artist = item.get("artist") or "Unknown Artist"
        event = item.get("event") or ""
        url = item.get("url") or ""
        source = item.get("source") or "secondary"
        low = item.get("low_price")
        high = item.get("high_price")
        baseline = item.get("baseline_price")
        pct = item.get("pct_change")
        updated = item.get("as_of") or ""

        title = f"{prefix} Price change detected"
        desc_parts = []
        if event:
            desc_parts.append(f"**Event:** {event}")
        desc_parts.append(f"**Artist:** {artist}")
        desc_parts.append(f"**Source:** {source}")
        if updated:
            desc_parts.append(f"**As of:** {updated}")

        embed = discord.Embed(description="\n".join(desc_parts))
        embed.set_footer(text="Viking AI • price monitor")

        def money(v: Any) -> str:
            try:
                return f"${float(v):,.2f}"
            except Exception:
                return "—"

        if low is not None:
            embed.add_field(name="Low", value=money(low), inline=True)
        if high is not None:
            embed.add_field(name="High", value=money(high), inline=True)
        if baseline is not None:
            embed.add_field(name="Baseline", value=money(baseline), inline=True)
        if pct is not None:
            try:
                embed.add_field(name="% Change", value=f"{float(pct):+.2f}%", inline=True)
            except Exception:
                pass
        if url:
            embed.add_field(name="Link", value=url, inline=False)

        ch = await _get_channel(PRICE_ALERT_CHANNEL_ID) if PRICE_ALERT_CHANNEL_ID else None
        if ch:
            await ch.send(content=title, embed=embed)
        else:
            await _send_to_mixed_channel(title, prefer="tour")
            if TOUR_SCAN_WEBHOOK_URL:
                await _send_webhook(TOUR_SCAN_WEBHOOK_URL, content="", embeds=[embed])
            elif VERIFIED_FAN_WEBHOOK_URL:
                await _send_webhook(VERIFIED_FAN_WEBHOOK_URL, content="", embeds=[embed])
    except Exception as e:
        logger.warning("post_price_alert failed: %s", e)

async def post_verified_fan_item(item: Dict[str, Any]) -> None:
    STATUS["last_vf_post_unix"] = time.time()
    prefix = VERIFIED_FAN_PREFIX or "[VF]"
    title = (item.get("title") or "Verified Fan update").strip()
    url = (item.get("url") or "").strip()
    when = (item.get("published") or item.get("date") or "").strip()
    src = (item.get("source") or "").strip()

    msg = f"{prefix} {title}"
    if when:
        msg += f"\n🕒 {when}"
    if src:
        msg += f"\n📰 {src}"
    if url:
        msg += f"\n{url}"

    if VERIFIED_FAN_WEBHOOK_URL:
        await _send_webhook(VERIFIED_FAN_WEBHOOK_URL, msg)
    else:
        ch = await _get_channel(VERIFIED_FAN_ALERT_CHANNEL_ID)
        if ch:
            await ch.send(msg)
        else:
            await _send_to_mixed_channel(msg, prefer="vf")

# --------------------
# Background loops
# --------------------
async def price_monitor_loop(interval_seconds: int = 900) -> None:
    if price_monitor is None:
        logger.info("price_monitor not available; skipping.")
        return
    logger.info("Price monitor loop started (%ss interval).", interval_seconds)
    while True:
        try:
            if hasattr(price_monitor, "poll_prices_once"):
                await asyncio.to_thread(price_monitor.poll_prices_once, post_price_alert)
            elif hasattr(price_monitor, "poll_once"):
                await asyncio.to_thread(price_monitor.poll_once, post_price_alert)
        except Exception as e:
            STATUS["last_error"] = f"price_monitor_loop: {e}"
            logger.warning("Price monitor tick failed: %s", e)
        await asyncio.sleep(interval_seconds)

async def verified_fan_loop() -> None:
    if verified_fan_monitor is None:
        logger.info("verified_fan_monitor not available; skipping.")
        return
    logger.info("Verified fan polling loop started (async task).")
    if hasattr(verified_fan_monitor, "start_verified_fan_loop"):
        await asyncio.to_thread(verified_fan_monitor.start_verified_fan_loop, post_verified_fan_item, VERIFIED_FAN_POLL_SECONDS)
    elif hasattr(verified_fan_monitor, "start_background_thread"):
        await asyncio.to_thread(verified_fan_monitor.start_background_thread, post_verified_fan_item, VERIFIED_FAN_POLL_SECONDS)
    else:
        if hasattr(verified_fan_monitor, "_poll_loop"):
            await asyncio.to_thread(verified_fan_monitor._poll_loop, post_verified_fan_item, VERIFIED_FAN_POLL_SECONDS)

async def health_watchdog() -> None:
    if STARTUP_NOTIFY:
        await _send_to_mixed_channel(
            f"✅ Viking AI online • rev `{_git_rev()}` • PID `{os.getpid()}`",
            prefer="tour",
        )
    while True:
        try:
            if HEALTH_PING_SECONDS > 0:
                logger.debug("health tick uptime=%ss mem=%.1fMB", _uptime_seconds(), _memory_mb())
        except Exception as e:
            STATUS["last_error"] = f"health_watchdog: {e}"
        await asyncio.sleep(max(HEALTH_PING_SECONDS, 30))

def start_tour_scan_monitor() -> None:
    if tour_scan_monitor is None:
        logger.info("tour_scan_monitor not available; skipping.")
        return

    starter = getattr(tour_scan_monitor, "start_background_thread", None) or getattr(tour_scan_monitor, "start_tour_scan_monitor", None)
    if not starter:
        logger.info("tour_scan_monitor present but no start_background_thread()/start_tour_scan_monitor() found; skipping.")
        return

    try:
        starter(
            discord_client=client,
            channel_id=TOUR_SCAN_ALERT_CHANNEL_ID,
            webhook_url=TOUR_SCAN_WEBHOOK_URL,
        )
        logger.info("Tour scan background thread started.")
    except TypeError:
        starter({"discord_client": client, "channel_id": TOUR_SCAN_ALERT_CHANNEL_ID, "webhook_url": TOUR_SCAN_WEBHOOK_URL})
        logger.info("Tour scan background thread started.")
    except Exception as e:
        STATUS["last_error"] = f"tour_scan_monitor start: {e}"
        logger.warning("Failed to start tour_scan_monitor: %s", e)

# --------------------
# Slash Commands
# --------------------
@tree.command(name="status", description="Show bot status (uptime, memory, monitors).")
async def status_cmd(interaction: discord.Interaction):
    data = {
        "rev": _git_rev(),
        "uptime_seconds": _uptime_seconds(),
        "memory_mb": round(_memory_mb(), 1),
        "monitors": {
            "price_monitor": bool(price_monitor),
            "verified_fan_monitor": bool(verified_fan_monitor),
            "tour_scan_monitor": bool(tour_scan_monitor),
        },
        "last_posts": {
            "price": STATUS.get("last_price_post_unix"),
            "vf": STATUS.get("last_vf_post_unix"),
            "tour": STATUS.get("last_tour_post_unix"),
        },
        "last_error": STATUS.get("last_error"),
    }
    await interaction.response.send_message(f"```json\n{json.dumps(data, indent=2, default=str)}\n```", ephemeral=True)

@tree.command(name="health", description="Health JSON dump (config presence + task state).")
async def health_cmd(interaction: discord.Interaction):
    def task_state(t: Optional[asyncio.Task]) -> Dict[str, Any]:
        if not t:
            return {"present": False}
        return {"present": True, "done": t.done(), "cancelled": t.cancelled(), "name": t.get_name()}

    health = {
        "rev": _git_rev(),
        "pid": os.getpid(),
        "uptime_seconds": _uptime_seconds(),
        "memory_mb": round(_memory_mb(), 1),
        "env": {
            "DISCORD_TOKEN_set": bool(DISCORD_TOKEN),
            "PRICE_ALERT_CHANNEL_ID": PRICE_ALERT_CHANNEL_ID,
            "VERIFIED_FAN_ALERT_CHANNEL_ID": VERIFIED_FAN_ALERT_CHANNEL_ID,
            "TOUR_SCAN_ALERT_CHANNEL_ID": TOUR_SCAN_ALERT_CHANNEL_ID,
            "VERIFIED_FAN_WEBHOOK_URL_set": bool(VERIFIED_FAN_WEBHOOK_URL),
            "TOUR_SCAN_WEBHOOK_URL_set": bool(TOUR_SCAN_WEBHOOK_URL),
            "PRICE_PREFIX": PRICE_PREFIX,
            "VERIFIED_FAN_PREFIX": VERIFIED_FAN_PREFIX,
            "TOUR_SCAN_PREFIX": TOUR_SCAN_PREFIX,
        },
        "tasks": {k: task_state(v) for k, v in _bg_tasks.items()},
        "last_error": STATUS.get("last_error"),
    }
    await interaction.response.send_message(f"```json\n{json.dumps(health, indent=2, default=str)}\n```", ephemeral=True)

@tree.command(name="debug", description="Quick debug dump (non-sensitive).")
async def debug_cmd(interaction: discord.Interaction):
    msg = (
        f"rev `{_git_rev()}` | uptime `{_uptime_seconds()}s` | mem `{_memory_mb():.1f}MB`\n"
        f"IDs: price={PRICE_ALERT_CHANNEL_ID} vf={VERIFIED_FAN_ALERT_CHANNEL_ID} tour={TOUR_SCAN_ALERT_CHANNEL_ID}\n"
        f"Webhooks: vf={bool(VERIFIED_FAN_WEBHOOK_URL)} tour={bool(TOUR_SCAN_WEBHOOK_URL)}\n"
        f"Prefixes: {PRICE_PREFIX} {VERIFIED_FAN_PREFIX} {TOUR_SCAN_PREFIX}\n"
        f"Token: {_redact(DISCORD_TOKEN)}"
    )
    await interaction.response.send_message(msg, ephemeral=True)

@tree.command(name="news_now", description="Get latest tour news for an artist.")
@app_commands.describe(artist="Artist name")
async def news_now_cmd(interaction: discord.Interaction, artist: str):
    await interaction.response.defer(thinking=True)
    if not get_tour_news:
        await interaction.followup.send("Tour news agent not available.")
        return
    try:
        text = await asyncio.to_thread(get_tour_news, artist)
        await interaction.followup.send(text[:1900])
    except Exception as e:
        await interaction.followup.send(f"Error: {e}")

@tree.command(name="events", description="Search Ticketmaster events for an artist.")
@app_commands.describe(artist="Artist name")
async def events_cmd(interaction: discord.Interaction, artist: str):
    await interaction.response.defer(thinking=True)
    if not search_events_for_artist:
        await interaction.followup.send("Ticketmaster search not available.")
        return
    try:
        results = await asyncio.to_thread(search_events_for_artist, artist)
        if not results:
            await interaction.followup.send("No events found.")
            return
        lines = []
        for e in results[:10]:
            eid = e.get("id") or e.get("event_id") or "?"
            name = e.get("name") or e.get("title") or "Event"
            date = e.get("date") or e.get("localDate") or ""
            venue = e.get("venue") or ""
            lines.append(f"• `{eid}` — {name} ({date}) {venue}".strip())
        await interaction.followup.send("\n".join(lines)[:1900])
    except Exception as e:
        await interaction.followup.send(f"Error: {e}")

@tree.command(name="eventdetails", description="Get Ticketmaster event details by id.")
@app_commands.describe(event_id="Ticketmaster event id")
async def eventdetails_cmd(interaction: discord.Interaction, event_id: str):
    await interaction.response.defer(thinking=True)
    if not get_event_details:
        await interaction.followup.send("Ticketmaster details not available.")
        return
    try:
        d = await asyncio.to_thread(get_event_details, event_id)
        if not d:
            await interaction.followup.send("No details found.")
            return
        await interaction.followup.send(f"```json\n{json.dumps(d, indent=2, default=str)[:1900]}\n```")
    except Exception as e:
        await interaction.followup.send(f"Error: {e}")

# --------------------
# Lifecycle
# --------------------
@client.event
async def on_ready():
    try:
        await tree.sync()
        logger.info("Slash commands synced.")
    except Exception as e:
        logger.warning("Slash sync failed: %s", e)

    logger.info("Logged in as %s", client.user)

    # Start monitors
    start_tour_scan_monitor()

    t1 = asyncio.create_task(verified_fan_loop(), name="verified_fan_loop")
    _task_guard("verified_fan_loop", t1)

    t2 = asyncio.create_task(price_monitor_loop(interval_seconds=900), name="price_monitor_loop")
    _task_guard("price_monitor_loop", t2)

    t3 = asyncio.create_task(health_watchdog(), name="health_watchdog")
    _task_guard("health_watchdog", t3)

# --------------------
# Entrypoint
# --------------------
def main():
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set.")
    client.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main()
