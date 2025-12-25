import os
import asyncio
import logging
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

try:
    from agents.spotify_agent import get_spotify_profile
except Exception:
    get_spotify_profile = None

try:
    from agents.youtube_agent import get_youtube_profile
except Exception:
    get_youtube_profile = None

try:
    from agents.tour_brain_v4 import get_event_intel
except Exception:
    get_event_intel = None

try:
    from orchestrator_v2 import run_llm_analysis
except Exception:
    run_llm_analysis = None


def _env_int(name: str, default: int = 0) -> int:
    try:
        val = (os.getenv(name) or "").strip()
        val = val.replace(" ", "")
        if not val or not val.isdigit():
            return default
        return int(val)
    except Exception:
        return default


# ---------- ENV ----------
load_dotenv()

DISCORD_TOKEN = (os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN") or "").strip()
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN (or DISCORD_BOT_TOKEN) missing (set it in /opt/viking-ai/.env)")

VERIFIED_FAN_ALERT_CHANNEL_ID = _env_int("VERIFIED_FAN_ALERT_CHANNEL_ID", 0)
TOUR_SCAN_ALERT_CHANNEL_ID = _env_int("TOUR_SCAN_ALERT_CHANNEL_ID", 0)
PRICE_ALERT_CHANNEL_ID = _env_int("PRICE_ALERT_CHANNEL_ID", 0)
PRICE_POLL_SECONDS = _env_int("PRICE_POLL_SECONDS", 900)  # default 15 min


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


# ---------- COMMANDS ----------
@tree.command(name="status", description="System health check")
async def status_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    lines = [
        f"✅ Bot online: **{client.user}**" if client.user else "✅ Bot online",
        f"PRICE_ALERT_CHANNEL_ID: `{PRICE_ALERT_CHANNEL_ID}`",
        f"VERIFIED_FAN_ALERT_CHANNEL_ID: `{VERIFIED_FAN_ALERT_CHANNEL_ID}`",
        f"TOUR_SCAN_ALERT_CHANNEL_ID: `{TOUR_SCAN_ALERT_CHANNEL_ID}`",
        f"price_monitor import: `{'OK' if price_monitor else 'MISSING'}`",
        f"verified_fan_monitor import: `{'OK' if verified_fan_monitor else 'MISSING'}`",
        f"tour_scan_monitor import: `{'OK' if tour_scan_monitor else 'MISSING'}`",
        f"ticketmaster_agent_v2: `{'OK' if search_events_for_artist and get_event_details else 'MISSING'}`",
        f"tour_news_agent_v3: `{'OK' if get_tour_news else 'MISSING'}`",
        f"seo_agent_v2: `{'OK' if run_seo_audit else 'MISSING'}`",
    ]
    await interaction.followup.send("\n".join(lines), ephemeral=True)


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


@tree.command(name="tour_news", description="Get recent tour news headlines")
@app_commands.describe(artist="Optional artist name to focus news, e.g. Tyler Childers")
async def tour_news_cmd(interaction: discord.Interaction, artist: Optional[str] = None):
    await interaction.response.defer()
    if not get_tour_news:
        await interaction.followup.send("❌ tour_news_agent_v3 not available (import failed).")
        return

    try:
        txt = await asyncio.to_thread(get_tour_news, artist=artist) if artist else await asyncio.to_thread(get_tour_news)
        await interaction.followup.send((txt or "No news returned.")[:1900])
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
        result = await asyncio.to_thread(run_seo_audit, url)
        await interaction.followup.send(str(result)[:1900], ephemeral=True)
    except Exception:
        logger.exception("seo_audit_cmd failed")
        await interaction.followup.send("❌ Error running SEO audit (check logs).", ephemeral=True)


@tree.command(name="intel", description="Deep tour intelligence for an artist")
@app_commands.describe(artist="Artist name")
async def intel_cmd(interaction: discord.Interaction, artist: str):
    await interaction.response.defer()
    if not (get_spotify_profile and get_youtube_profile and get_event_intel and run_llm_analysis):
        await interaction.followup.send("❌ Intel stack missing (spotify/youtube/tour_brain/orchestrator import failed).")
        return

    try:
        spotify = await asyncio.to_thread(get_spotify_profile, artist)
        youtube = await asyncio.to_thread(get_youtube_profile, artist)

        event_shell = {"artist": artist, "spotify": spotify, "youtube": youtube}
        intel = await get_event_intel(event_shell, artist)

        prompt = (
            f"Artist: {artist}\n"
            f"Spotify: {spotify}\n"
            f"YouTube: {youtube}\n"
            f"Intel: {intel}\n"
            "Give a concise touring analysis."
        )
        analysis = await asyncio.to_thread(run_llm_analysis, prompt)

        msg = (
            f"📊 **Viking AI Intel — {artist}**\n\n"
            f"{str(intel)[:900]}\n\n"
            f"🧠 **Viking Analysis:**\n{str(analysis)[:900]}"
        )
        await interaction.followup.send(msg[:1900])
    except Exception as e:
        logger.exception("intel_cmd failed")
        await interaction.followup.send(f"❌ Touring Brain error: `{e}`", ephemeral=True)


# ---------- BACKGROUND MONITORS ----------
async def _start_price_monitor() -> None:
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
            await channel.send(f"**{title}**\n{body}"[:1900])
        except Exception:
            logger.exception("Failed posting price alert to Discord")

    async def _loop():
        logger.info("Price monitor loop started (%ss interval).", PRICE_POLL_SECONDS)
        while True:
            try:
                alerts = price_monitor.poll_prices_once()
                if alerts:
                    if isinstance(alerts, dict):
                        alerts = [alerts]
                    for a in alerts:
                        await _post_price_alert(a)
            except Exception:
                logger.exception("price monitor loop failed")
            await asyncio.sleep(PRICE_POLL_SECONDS)

    asyncio.create_task(_loop())


def _start_verified_fan_monitor() -> None:
    if not VERIFIED_FAN_ALERT_CHANNEL_ID:
        logger.info("Verified fan disabled (VERIFIED_FAN_ALERT_CHANNEL_ID not set).")
        return
    if not verified_fan_monitor:
        logger.warning("Verified fan disabled (verified_fan_monitor import failed).")
        return

    starter = getattr(verified_fan_monitor, "start_verified_fan_monitor", None)
    if callable(starter):
        starter(discord_client=client, channel_id=VERIFIED_FAN_ALERT_CHANNEL_ID)
        logger.info("Verified fan polling loop started.")
        return

    poller = getattr(verified_fan_monitor, "poll_verified_fan_loop", None)
    if poller:
        asyncio.create_task(poller(discord_client=client, channel_id=VERIFIED_FAN_ALERT_CHANNEL_ID))
        logger.info("Verified fan polling loop started (async task).")
        return

    logger.warning("Verified fan disabled (no starter/poller found).")


def _start_tour_scan_monitor() -> None:
    if not tour_scan_monitor:
        logger.info("Tour scan disabled (tour_scan_monitor import failed).")
        return

    starter = getattr(tour_scan_monitor, "start_tour_scan_monitor", None)
    if callable(starter):
        starter(discord_client=client, channel_id=TOUR_SCAN_ALERT_CHANNEL_ID)
        logger.info("Tour scan background thread started.")
        return

    logger.info("tour_scan_monitor present but no start_tour_scan_monitor() found; skipping.")


# ---------- STARTUP ----------
@client.event
async def on_ready():
    global _started_background
    if _started_background:
        return
    _started_background = True

    await tree.sync()
    logger.info("Slash commands synced.")
    logger.info("Logged in as %s", client.user)

    _start_verified_fan_monitor()
    _start_tour_scan_monitor()
    await _start_price_monitor()


def main():
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
