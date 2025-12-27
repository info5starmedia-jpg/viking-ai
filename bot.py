import os
import asyncio
import logging
import inspect
from typing import Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

# --------------------
# Optional imports (guarded)
# --------------------
try:
    import price_monitor
except Exception:
    price_monitor = None

try:
    import verified_fan_monitor
except Exception:
    verified_fan_monitor = None

try:
    import tour_scan_monitor
except Exception:
    tour_scan_monitor = None

try:
    from ticketmaster_agent_v2 import search_events_for_artist, get_event_details
except Exception:
    search_events_for_artist = None
    get_event_details = None

try:
    from agents.tour_news_agent_v3 import get_tour_news
except Exception:
    get_tour_news = None

try:
    from agents.seo_agent_v2 import run_seo_audit
except Exception:
    run_seo_audit = None


def _env_int(name: str, default: int = 0) -> int:
    try:
        v = (os.getenv(name) or "").strip()
        return int(v) if v else default
    except Exception:
        return default


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


# --------------------
# Env + logging
# --------------------
load_dotenv()

DISCORD_TOKEN = _env_str("DISCORD_TOKEN") or _env_str("DISCORD_BOT_TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN (or DISCORD_BOT_TOKEN) missing in /opt/viking-ai/.env")

PRICE_ALERT_CHANNEL_ID = _env_int("PRICE_ALERT_CHANNEL_ID", 0)
VERIFIED_FAN_ALERT_CHANNEL_ID = _env_int("VERIFIED_FAN_ALERT_CHANNEL_ID", 0)
TOUR_SCAN_ALERT_CHANNEL_ID = _env_int("TOUR_SCAN_ALERT_CHANNEL_ID", 0)
TOUR_SCAN_WEBHOOK_URL = _env_str("TOUR_SCAN_WEBHOOK_URL", "")
PRICE_POLL_SECONDS = _env_int("PRICE_POLL_SECONDS", 900)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("viking_ai")


# --------------------
# Discord client
# --------------------
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

_started_background = False


# --------------------
# Commands
# --------------------
@tree.command(name="status", description="System health check")
async def status_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    bits = []
    bits.append(f"✅ Bot online: **{client.user}**" if client.user else "✅ Bot online")
    bits.append(f"PRICE_ALERT_CHANNEL_ID: `{PRICE_ALERT_CHANNEL_ID}`")
    bits.append(f"VERIFIED_FAN_ALERT_CHANNEL_ID: `{VERIFIED_FAN_ALERT_CHANNEL_ID}`")
    bits.append(f"TOUR_SCAN_ALERT_CHANNEL_ID: `{TOUR_SCAN_ALERT_CHANNEL_ID}`")
    bits.append(f"TOUR_SCAN_WEBHOOK_URL set: `{'YES' if bool(TOUR_SCAN_WEBHOOK_URL) else 'NO'}`")
    bits.append(f"price_monitor import: `{'OK' if price_monitor else 'MISSING'}`")
    bits.append(f"verified_fan_monitor import: `{'OK' if verified_fan_monitor else 'MISSING'}`")
    bits.append(f"tour_scan_monitor import: `{'OK' if tour_scan_monitor else 'MISSING'}`")
    await interaction.followup.send("\n".join(bits), ephemeral=True)


@tree.command(name="events", description="Search Ticketmaster events for an artist")
@app_commands.describe(artist="Artist name, e.g. Tyler Childers")
async def events_cmd(interaction: discord.Interaction, artist: str):
    await interaction.response.defer()
    if not search_events_for_artist:
        await interaction.followup.send("❌ Ticketmaster agent not available (ticketmaster_agent_v2 import failed).")
        return

    try:
        events = search_events_for_artist(artist)
        if not events:
            await interaction.followup.send(f"No events found for **{artist}**.")
            return

        out = [f"**Events for {artist}:**"]
        for e in events[:10]:
            name = e.get("name") or "Unknown"
            eid = e.get("id") or "N/A"
            date = (e.get("dates") or {}).get("start", {}).get("localDate") or ""
            venue = (((e.get("_embedded") or {}).get("venues") or [{}])[0]).get("name") or ""
            out.append(f"- `{eid}` — **{name}** — {date} — {venue}".strip())

        if len(events) > 10:
            out.append(f"...and {len(events) - 10} more.")
        await interaction.followup.send("\n".join(out))
    except Exception:
        logger.exception("events_cmd failed")
        await interaction.followup.send("❌ Error searching events (check logs).")


@tree.command(name="eventdetails", description="Get Ticketmaster event details by id")
@app_commands.describe(event_id="Ticketmaster event id")
async def eventdetails_cmd(interaction: discord.Interaction, event_id: str):
    await interaction.response.defer()
    if not get_event_details:
        await interaction.followup.send("❌ Ticketmaster agent not available (ticketmaster_agent_v2 import failed).")
        return
    try:
        e = get_event_details(event_id)
        if not e:
            await interaction.followup.send("No details returned.")
            return
        name = e.get("name") or "Unknown"
        date = (e.get("dates") or {}).get("start", {}).get("localDate") or ""
        venue = (((e.get("_embedded") or {}).get("venues") or [{}])[0]).get("name") or ""
        url = e.get("url") or ""
        await interaction.followup.send(f"**{name}**\nDate: {date}\nVenue: {venue}\n{url}".strip())
    except Exception:
        logger.exception("eventdetails_cmd failed")
        await interaction.followup.send("❌ Error fetching event details (check logs).")


# --------------------
# Monitors
# --------------------
async def _start_price_monitor():
    if not PRICE_ALERT_CHANNEL_ID:
        logger.info("Price monitor disabled (PRICE_ALERT_CHANNEL_ID not set).")
        return
    if not price_monitor:
        logger.warning("Price monitor disabled (price_monitor import failed).")
        return

    async def _post_price_alert(alert: dict) -> None:
        try:
            channel = client.get_channel(PRICE_ALERT_CHANNEL_ID) or await client.fetch_channel(PRICE_ALERT_CHANNEL_ID)
            title = alert.get("title") or "Price Alert"
            body = alert.get("message") or alert.get("text") or str(alert)
            await channel.send(f"**{title}**\n{body}")
        except Exception:
            logger.exception("Failed posting price alert to Discord")

    async def _price_loop():
        logger.info("Price monitor loop started (%ss interval).", PRICE_POLL_SECONDS)
        while True:
            try:
                alerts = price_monitor.poll_prices_once()
                if alerts:
                    if isinstance(alerts, dict):
                        alerts = [alerts]
                    for alert in alerts:
                        await _post_price_alert(alert)
            except Exception:
                logger.exception("price monitor loop failed")
            await asyncio.sleep(PRICE_POLL_SECONDS)

    asyncio.create_task(_price_loop())


async def _start_verified_fan_monitor():
    if not verified_fan_monitor:
        logger.info("Verified fan disabled (verified_fan_monitor import failed).")
        return
    if not VERIFIED_FAN_ALERT_CHANNEL_ID:
        logger.info("Verified fan disabled (VERIFIED_FAN_ALERT_CHANNEL_ID not set).")
        return

    coro = getattr(verified_fan_monitor, "poll_verified_fan_loop", None)
    if callable(coro):
        asyncio.create_task(coro(discord_client=client, channel_id=VERIFIED_FAN_ALERT_CHANNEL_ID))
        logger.info("Verified fan polling loop started (async task).")
        return

    starter = getattr(verified_fan_monitor, "start_verified_fan_monitor", None)
    if callable(starter):
        starter(discord_client=client, channel_id=VERIFIED_FAN_ALERT_CHANNEL_ID)
        logger.info("Verified fan polling loop started (thread).")
        return

    logger.info("verified_fan_monitor present but no recognized starter found; skipping.")


def _start_tour_scan_monitor() -> None:
    if not tour_scan_monitor:
        logger.info("Tour scan disabled (tour_scan_monitor import failed).")
        return

    # Prefer start_background_thread()
    starter = getattr(tour_scan_monitor, "start_background_thread", None)
    if not callable(starter):
        starter = getattr(tour_scan_monitor, "start_tour_scan_monitor", None)

    if not callable(starter):
        logger.info("tour_scan_monitor present but no start_background_thread()/start_tour_scan_monitor() found; skipping.")
        return

    kwargs = {}
    try:
        sig = inspect.signature(starter)
        params = set(sig.parameters.keys())

        if "discord_client" in params:
            kwargs["discord_client"] = client
        if "channel_id" in params:
            kwargs["channel_id"] = TOUR_SCAN_ALERT_CHANNEL_ID
        if "webhook_url" in params:
            kwargs["webhook_url"] = TOUR_SCAN_WEBHOOK_URL
    except Exception:
        kwargs = {"discord_client": client, "channel_id": TOUR_SCAN_ALERT_CHANNEL_ID, "webhook_url": TOUR_SCAN_WEBHOOK_URL}

    starter(**kwargs)
    logger.info("Tour scan background thread started.")


@client.event
async def on_ready():
    global _started_background
    if _started_background:
        return
    _started_background = True

    try:
        await tree.sync()
        logger.info("Slash commands synced.")
    except Exception:
        logger.exception("Slash commands sync failed")

    logger.info("Logged in as %s", client.user)

    await _start_verified_fan_monitor()
    _start_tour_scan_monitor()
    await _start_price_monitor()


def main():
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
