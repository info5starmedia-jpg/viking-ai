import os
import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

# Project modules (ok if some are missing; we guard imports)
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
    val = os.getenv(name, "")
    try:
        val = (val or "").strip()
        if not val or not val.isdigit():
            return default
        return int(val)
    except Exception:
        return default


# ---------- ENV ----------
load_dotenv()

DISCORD_TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
VERIFIED_FAN_ALERT_CHANNEL_ID = _env_int("VERIFIED_FAN_ALERT_CHANNEL_ID", 0)
TOUR_SCAN_ALERT_CHANNEL_ID = _env_int("TOUR_SCAN_ALERT_CHANNEL_ID", 0)
PRICE_ALERT_CHANNEL_ID = _env_int("PRICE_ALERT_CHANNEL_ID", 0)
PRICE_POLL_SECONDS = _env_int("PRICE_POLL_SECONDS", 900)  # default 15 min

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN missing (set it in /opt/viking-ai/.env)")


# ---------- LOGGING ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("viking_ai")


# ---------- DISCORD ----------
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

_started_background = False


@tree.command(name="status", description="System health check")
async def status_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    bits = []
    bits.append(f"✅ Bot online: **{client.user}**" if client.user else "✅ Bot online")
    bits.append(f"PRICE_ALERT_CHANNEL_ID: `{PRICE_ALERT_CHANNEL_ID}`")
    bits.append(f"VERIFIED_FAN_ALERT_CHANNEL_ID: `{VERIFIED_FAN_ALERT_CHANNEL_ID}`")
    bits.append(f"TOUR_SCAN_ALERT_CHANNEL_ID: `{TOUR_SCAN_ALERT_CHANNEL_ID}`")
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

        # Keep message short to avoid Discord limits
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


@tree.command(name="tour_news", description="Get recent tour news headlines")
@app_commands.describe(artist="Optional artist name to focus news, e.g. Tyler Childers")
async def tour_news_cmd(interaction: discord.Interaction, artist: Optional[str] = None):
    await interaction.response.defer()
    if not get_tour_news:
        await interaction.followup.send("❌ tour_news_agent_v3 not available (import failed).")
        return
    try:
        txt = get_tour_news(artist=artist) if artist else get_tour_news()
        if not txt:
            await interaction.followup.send("No news returned.")
            return
        # Ensure safe Discord message length
        await interaction.followup.send(txt[:1900])
    except Exception:
        logger.exception("tour_news_cmd failed")
        await interaction.followup.send("❌ Error pulling tour news (check logs).")


@tree.command(name="seo_audit", description="Run a quick SEO audit on a URL")
@app_commands.describe(url="Website URL, e.g. https://example.com")
async def seo_audit_cmd(interaction: discord.Interaction, url: str):
    await interaction.response.defer(ephemeral=True)
    if not run_seo_audit:
        await interaction.followup.send("❌ seo_agent_v2 not available (import failed).", ephemeral=True)
        return
    try:
        result = run_seo_audit(url)
        text = str(result)
        await interaction.followup.send(text[:1900], ephemeral=True)
    except Exception:
        logger.exception("seo_audit_cmd failed")
        await interaction.followup.send("❌ Error running SEO audit (check logs).", ephemeral=True)


async def _start_price_monitor(loop: asyncio.AbstractEventLoop):
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


@client.event
async def on_ready():
    global _started_background
    if _started_background:
        return
    _started_background = True

    # Sync commands
    try:
        await tree.sync()
        logger.info("Slash commands synced.")
    except Exception:
        logger.exception("Slash command sync failed.")

    logger.info("Logged in as %s", client.user)

    # Start tour scan background if module supports it
    try:
        if tour_scan_monitor:
            # try common function names safely
            if hasattr(tour_scan_monitor, "start_background_thread"):
                tour_scan_monitor.start_background_thread(client, TOUR_SCAN_ALERT_CHANNEL_ID)
                logger.info("Tour scan background thread started.")
            elif hasattr(tour_scan_monitor, "start"):
                tour_scan_monitor.start(client, TOUR_SCAN_ALERT_CHANNEL_ID)
                logger.info("Tour scan started.")
            else:
                logger.info("tour_scan_monitor present but no recognized start function.")
    except Exception:
        logger.exception("Starting tour scan failed")

    # Start verified fan polling if module supports it
    try:
        if verified_fan_monitor and VERIFIED_FAN_ALERT_CHANNEL_ID:
            if hasattr(verified_fan_monitor, "start_polling_loop"):
                verified_fan_monitor.start_polling_loop(client, VERIFIED_FAN_ALERT_CHANNEL_ID)
                logger.info("Verified fan polling loop started.")
            elif hasattr(verified_fan_monitor, "start"):
                verified_fan_monitor.start(client, VERIFIED_FAN_ALERT_CHANNEL_ID)
                logger.info("Verified fan started.")
            else:
                logger.info("verified_fan_monitor present but no recognized start function.")
        elif verified_fan_monitor:
            logger.info("Verified fan disabled (VERIFIED_FAN_ALERT_CHANNEL_ID not set).")
    except Exception:
        logger.exception("Starting verified fan monitor failed")

    # Start price monitor (async)
    try:
        loop = asyncio.get_running_loop()
        await _start_price_monitor(loop)
    except Exception:
        logger.exception("Starting price monitor failed")


def main():
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
