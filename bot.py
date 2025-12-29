import os
import asyncio
import base64
import json
import logging
import math
import re
import subprocess
import threading
import time
from typing import Any, Dict, Optional, List, Tuple


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
    import viking_db
except Exception:
    viking_db = None

try:
    import usage_db
except Exception:
    usage_db = None

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

# Artist intel integrations
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_CSE_API_KEY", "").strip()
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "").strip()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()

ARTIST_CACHE_PATH = "/opt/viking-ai/artist_profiles.json"
URL_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
STATS_CACHE_TTL_SECONDS = 24 * 60 * 60

# Intel refresh
INTEL_REFRESH_SECONDS = int(os.getenv("INTEL_REFRESH_SECONDS", "21600") or "21600")
INTEL_REFRESH_MAX_ARTISTS = int(os.getenv("INTEL_REFRESH_MAX_ARTISTS", "30") or "30")
INTEL_REFRESH_CONCURRENCY = int(os.getenv("INTEL_REFRESH_CONCURRENCY", "3") or "3")

# Paid tiers
DEFAULT_TIER = (os.getenv("DEFAULT_TIER", "FREE") or "FREE").strip().upper()
ADMIN_USER_IDS = {i.strip() for i in (os.getenv("ADMIN_USER_IDS", "") or "").split(",") if i.strip()}
PRO_GUILD_IDS = {i.strip() for i in (os.getenv("PRO_GUILD_IDS", "") or "").split(",") if i.strip()}

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
    "last_intel_refresh_unix": None,
    "last_intel_refresh_summary": None,
}

_artist_cache_lock = threading.Lock()
_SPOTIFY_TOKEN: Optional[str] = None
_SPOTIFY_TOKEN_EXPIRY: float = 0.0

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

def _normalize_tier(tier: Optional[str]) -> str:
    return (tier or "FREE").strip().upper() or "FREE"

def _tier_value(tier: Optional[str]) -> int:
    order = {"FREE": 0, "PRO": 1, "ADMIN": 2}
    return order.get(_normalize_tier(tier), 0)

def _get_guild_tier_sync(guild_id: Optional[str]) -> str:
    if not guild_id:
        return DEFAULT_TIER
    if usage_db:
        try:
            override = usage_db.get_guild_tier_override(str(guild_id))
        except Exception:
            override = None
        if override:
            return _normalize_tier(override)
    if str(guild_id) in PRO_GUILD_IDS:
        return "PRO"
    return DEFAULT_TIER

async def _get_effective_tier(interaction: discord.Interaction) -> str:
    user_id = str(interaction.user.id) if interaction.user else ""
    if user_id and user_id in ADMIN_USER_IDS:
        return "ADMIN"
    guild_id = str(interaction.guild_id) if interaction.guild_id else None
    return await asyncio.to_thread(_get_guild_tier_sync, guild_id)

async def _send_ephemeral(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)

async def _require_tier(interaction: discord.Interaction, min_tier: str) -> bool:
    effective = await _get_effective_tier(interaction)
    if _tier_value(effective) >= _tier_value(min_tier):
        return True
    await _send_ephemeral(
        interaction,
        f"🔒 `{min_tier}` required. Current tier: `{effective}`. Contact admin to upgrade.",
    )
    return False

async def _record_usage(
    command: str,
    interaction: discord.Interaction,
    ok: bool,
    latency_ms: int,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    if not usage_db:
        return
    guild_id = str(interaction.guild_id) if interaction.guild_id else None
    channel_id = str(interaction.channel_id) if interaction.channel_id else None
    user_id = str(interaction.user.id) if interaction.user else None
    await asyncio.to_thread(
        usage_db.record_usage,
        command,
        guild_id,
        channel_id,
        user_id,
        ok,
        latency_ms,
        extra or {},
    )

# --------------------
# Artist intel helpers
# --------------------
def normalize_artist_key(artist: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (artist or "").lower()).strip()
    return re.sub(r"\s+", " ", cleaned)

def _ensure_cache_dir() -> None:
    try:
        os.makedirs(os.path.dirname(ARTIST_CACHE_PATH), exist_ok=True)
    except Exception:
        return

def load_cache() -> Dict[str, Any]:
    with _artist_cache_lock:
        if not os.path.exists(ARTIST_CACHE_PATH):
            return {}
        try:
            with open(ARTIST_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            if isinstance(data, dict):
                return data
        except Exception:
            return {}
    return {}

def save_cache(cache: Dict[str, Any]) -> None:
    with _artist_cache_lock:
        _ensure_cache_dir()
        try:
            with open(ARTIST_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2, sort_keys=True)
        except Exception:
            return

def _cache_get_artist(cache: Dict[str, Any], artist_key: str) -> Dict[str, Any]:
    entry = cache.get(artist_key)
    if isinstance(entry, dict):
        return entry
    return {}

def _cache_set_artist(cache: Dict[str, Any], artist_key: str, data: Dict[str, Any]) -> None:
    cache[artist_key] = data

def _is_fresh(ts: Optional[float], ttl_seconds: int) -> bool:
    if not ts:
        return False
    return (time.time() - float(ts)) <= ttl_seconds

def _get_cached_urls(entry: Dict[str, Any]) -> Dict[str, Any]:
    urls = entry.get("urls")
    if isinstance(urls, dict) and _is_fresh(urls.get("updated_at"), URL_CACHE_TTL_SECONDS):
        return urls
    return {}

def _get_cached_stats(entry: Dict[str, Any]) -> Dict[str, Any]:
    stats = entry.get("stats")
    if isinstance(stats, dict) and _is_fresh(stats.get("updated_at"), STATS_CACHE_TTL_SECONDS):
        return stats
    return {}

def tavily_search(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    if not TAVILY_API_KEY or not query:
        return []
    import requests
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "max_results": max(1, int(max_results)),
    }
    try:
        resp = requests.post("https://api.tavily.com/search", json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json() or {}
        results = data.get("results") or []
        return [r for r in results if isinstance(r, dict)]
    except Exception:
        return []

def google_cse_search(query: str, num: int = 5) -> List[Dict[str, Any]]:
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID or not query:
        return []
    import requests
    params = {
        "key": GOOGLE_CSE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": query,
        "num": max(1, min(10, int(num))),
    }
    try:
        resp = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json() or {}
        items = data.get("items") or []
        return [i for i in items if isinstance(i, dict)]
    except Exception:
        return []

def _extract_urls(results: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    urls: List[Tuple[str, str]] = []
    for r in results or []:
        url = (r.get("url") or r.get("link") or "").strip()
        title = (r.get("title") or r.get("snippet") or r.get("content") or "").strip()
        if url:
            urls.append((url, title))
    return urls

def _find_first_url(urls: List[Tuple[str, str]], predicate) -> Optional[str]:
    for url, title in urls:
        try:
            if predicate(url, title):
                return url
        except Exception:
            continue
    return None

def _is_social_domain(url: str, domain: str) -> bool:
    return domain in url.lower()

def _discover_profile_links(artist: str) -> Dict[str, Any]:
    urls: List[Tuple[str, str]] = []
    tavily = tavily_search(f"{artist} official site presale signup verified fan youtube spotify tiktok", max_results=6)
    urls.extend(_extract_urls(tavily))

    if len(urls) < 4:
        cse = google_cse_search(f"{artist} official site presale signup verified fan youtube spotify tiktok", num=6)
        urls.extend(_extract_urls(cse))

    def is_official_site(u: str, _t: str) -> bool:
        u_lower = u.lower()
        if any(d in u_lower for d in ["wikipedia.org", "facebook.com", "instagram.com", "twitter.com", "x.com"]):
            return False
        if any(d in u_lower for d in ["youtube.com", "youtu.be", "spotify.com", "tiktok.com", "ticketmaster.com", "livenation.com"]):
            return False
        return True

    def is_presale(u: str, t: str) -> bool:
        text = f"{u} {t}".lower()
        return any(k in text for k in ["presale", "verified fan", "registration", "signup", "register", "fan registration"])

    official_site = _find_first_url(urls, is_official_site)
    presale_url = _find_first_url(urls, is_presale)
    youtube_url = _find_first_url(urls, lambda u, _t: _is_social_domain(u, "youtube.com") or _is_social_domain(u, "youtu.be"))
    spotify_url = _find_first_url(urls, lambda u, _t: _is_social_domain(u, "spotify.com"))
    tiktok_url = _find_first_url(urls, lambda u, _t: _is_social_domain(u, "tiktok.com"))

    tiktok_followers = ""
    for u, t in urls:
        if tiktok_url and tiktok_url in u:
            match = re.search(r"([\d,.]+)\s*followers", t.lower())
            if match:
                tiktok_followers = match.group(1)
                break

    return {
        "official_site": official_site or "",
        "presale_url": presale_url or "",
        "youtube_url": youtube_url or "",
        "spotify_url": spotify_url or "",
        "tiktok_url": tiktok_url or "",
        "tiktok_followers": tiktok_followers,
        "updated_at": time.time(),
    }

def _resolve_youtube_channel_id(url: str) -> Optional[str]:
    if not url or not YOUTUBE_API_KEY:
        return None
    import requests
    u = url.strip()
    if "/channel/" in u:
        return u.split("/channel/")[-1].split("/")[0]
    handle_match = re.search(r"/@([^/?]+)", u)
    if handle_match:
        query = handle_match.group(1)
    else:
        user_match = re.search(r"/user/([^/?]+)", u)
        query = user_match.group(1) if user_match else ""
    if not query:
        return None
    params = {
        "part": "snippet",
        "type": "channel",
        "q": query,
        "maxResults": 1,
        "key": YOUTUBE_API_KEY,
    }
    try:
        resp = requests.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json() or {}
        items = data.get("items") or []
        if items:
            return (items[0].get("id") or {}).get("channelId")
    except Exception:
        return None
    return None

def _fetch_youtube_stats(channel_url: str) -> Dict[str, Any]:
    if not channel_url or not YOUTUBE_API_KEY:
        return {"updated_at": time.time()}
    import requests
    channel_id = _resolve_youtube_channel_id(channel_url)
    if not channel_id:
        return {"updated_at": time.time()}
    params = {
        "part": "statistics",
        "id": channel_id,
        "key": YOUTUBE_API_KEY,
    }
    try:
        resp = requests.get("https://www.googleapis.com/youtube/v3/channels", params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json() or {}
        items = data.get("items") or []
        if items:
            stats = (items[0].get("statistics") or {})
            return {
                "channel_id": channel_id,
                "subscribers": int(stats.get("subscriberCount") or 0),
                "views": int(stats.get("viewCount") or 0),
                "updated_at": time.time(),
            }
    except Exception:
        return {"updated_at": time.time()}
    return {"updated_at": time.time()}

def _spotify_get_token() -> Optional[str]:
    global _SPOTIFY_TOKEN, _SPOTIFY_TOKEN_EXPIRY
    if _SPOTIFY_TOKEN and time.time() < _SPOTIFY_TOKEN_EXPIRY:
        return _SPOTIFY_TOKEN
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    import requests
    auth = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode("utf-8")).decode("utf-8")
    headers = {"Authorization": f"Basic {auth}"}
    data = {"grant_type": "client_credentials"}
    try:
        resp = requests.post("https://accounts.spotify.com/api/token", headers=headers, data=data, timeout=20)
        resp.raise_for_status()
        payload = resp.json() or {}
        token = payload.get("access_token")
        expires_in = int(payload.get("expires_in") or 3600)
        if token:
            _SPOTIFY_TOKEN = token
            _SPOTIFY_TOKEN_EXPIRY = time.time() + max(300, expires_in - 60)
            return token
    except Exception:
        return None
    return None

def _fetch_spotify_stats(artist: str) -> Dict[str, Any]:
    token = _spotify_get_token()
    if not token or not artist:
        return {"updated_at": time.time()}
    import requests
    headers = {"Authorization": f"Bearer {token}"}
    params = {"q": artist, "type": "artist", "limit": 1}
    try:
        resp = requests.get("https://api.spotify.com/v1/search", headers=headers, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json() or {}
        items = (data.get("artists") or {}).get("items") or []
        if items:
            item = items[0]
            followers = (item.get("followers") or {}).get("total") or 0
            popularity = item.get("popularity") or 0
            return {
                "spotify_url": (item.get("external_urls") or {}).get("spotify") or "",
                "followers": int(followers),
                "popularity": int(popularity),
                "updated_at": time.time(),
            }
    except Exception:
        return {"updated_at": time.time()}
    return {"updated_at": time.time()}

def _compute_artist_score(
    artist: str,
    spotify_stats: Dict[str, Any],
    youtube_stats: Dict[str, Any],
) -> Tuple[int, int]:
    score = 10.0
    if viking_db and hasattr(viking_db, "get_artist_counts_time_aware"):
        try:
            counts = viking_db.get_artist_counts_time_aware(artist)
            score += min(30.0, float(counts.get("events_30d", 0)) * 5.0)
            score += min(20.0, float(counts.get("news_30d", 0)) * 4.0)
        except Exception:
            pass

    sp_pop = float(spotify_stats.get("popularity") or 0)
    sp_followers = float(spotify_stats.get("followers") or 0)
    yt_subs = float(youtube_stats.get("subscribers") or 0)
    yt_views = float(youtube_stats.get("views") or 0)

    score += sp_pop * 0.4
    score += min(20.0, math.log10(sp_followers + 1) * 4.0)
    score += min(15.0, math.log10(yt_subs + 1) * 3.0)
    score += min(10.0, math.log10(yt_views + 1) * 2.0)

    score = max(0.0, min(100.0, score))
    stars = max(1, min(5, int(round(score / 20.0))))
    return int(round(score)), stars

def _best_cities_for_artist(score: int) -> List[str]:
    tiers = [
        ("New York, NY", 95),
        ("Los Angeles, CA", 95),
        ("Chicago, IL", 90),
        ("Dallas, TX", 88),
        ("Houston, TX", 86),
        ("Atlanta, GA", 85),
        ("San Francisco, CA", 85),
        ("Seattle, WA", 82),
        ("Boston, MA", 82),
        ("Philadelphia, PA", 80),
        ("Miami, FL", 80),
        ("Denver, CO", 78),
        ("Phoenix, AZ", 76),
        ("Minneapolis, MN", 74),
        ("Detroit, MI", 72),
        ("Nashville, TN", 72),
        ("Austin, TX", 70),
        ("Portland, OR", 70),
    ]
    adjusted = []
    for city, tier in tiers:
        adjusted.append((city, tier + (score - 50) * 0.2))
    adjusted.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in adjusted[:8]]

def _city_tier_score(city: str) -> int:
    city_lower = (city or "").lower()
    if any(key in city_lower for key in ["new york", "los angeles", "chicago"]):
        return 95
    if any(key in city_lower for key in ["dallas", "houston", "atlanta", "san francisco"]):
        return 85
    if any(key in city_lower for key in ["seattle", "boston", "philadelphia", "miami"]):
        return 80
    return 70

def _sellout_probability(
    artist_score: int,
    city_tier: int,
    capacity: Optional[int],
    presale_bonus: bool,
    recent_events_30d: float,
    news_30d: float,
) -> int:
    popularity_bonus = max(0.0, min(100.0, float(artist_score)))
    base = 0.55 * float(artist_score) + 0.35 * float(city_tier) + 0.10 * popularity_bonus

    if capacity:
        if capacity < 4000:
            base += 18
        elif capacity < 8000:
            base += 10
        elif capacity < 12000:
            base += 6
        elif capacity < 20000:
            base += 2
        else:
            base -= 4

    if presale_bonus:
        base += 4

    momentum = min(10.0, float(recent_events_30d) * 2.0 + float(news_30d) * 1.5)
    base += momentum

    return max(0, min(100, int(round(base))))

def build_artist_intel(artist: str) -> Dict[str, Any]:
    artist = (artist or "").strip()
    if not artist:
        return {}
    cache = load_cache()
    key = normalize_artist_key(artist)
    entry = _cache_get_artist(cache, key)
    entry["artist_name"] = artist
    entry["last_used_at"] = time.time()

    urls = _get_cached_urls(entry)
    if not urls:
        urls = _discover_profile_links(artist)
        entry["urls"] = urls

    stats = _get_cached_stats(entry)
    if not stats:
        youtube_stats = _fetch_youtube_stats(urls.get("youtube_url", ""))
        spotify_stats = _fetch_spotify_stats(artist)
        stats = {
            "youtube": youtube_stats,
            "spotify": spotify_stats,
            "updated_at": time.time(),
        }
        entry["stats"] = stats

    spotify_stats = stats.get("spotify", {}) if isinstance(stats, dict) else {}
    youtube_stats = stats.get("youtube", {}) if isinstance(stats, dict) else {}
    score, stars = _compute_artist_score(artist, spotify_stats, youtube_stats)

    entry["computed"] = {
        "score": score,
        "stars": stars,
        "best_cities": _best_cities_for_artist(score),
        "updated_at": time.time(),
    }

    _cache_set_artist(cache, key, entry)
    save_cache(cache)

    return {
        "artist": artist,
        "urls": urls,
        "stats": stats,
        "score": score,
        "stars": stars,
        "best_cities": entry["computed"]["best_cities"],
    }

def _format_stat_number(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return "—"

def _format_artist_intel_message(artist: str, intel: Dict[str, Any], events: List[Dict[str, Any]]) -> str:
    stars = intel.get("stars") or 0
    score = intel.get("score") or 0
    urls = intel.get("urls") or {}
    stats = intel.get("stats") or {}
    spotify_stats = (stats.get("spotify") or {}) if isinstance(stats, dict) else {}
    youtube_stats = (stats.get("youtube") or {}) if isinstance(stats, dict) else {}

    lines = [f"🎯 Artist Intel v1.1 — **{artist}**"]
    lines.append(f"⭐ Stars: {stars} | Score: {score}/100")

    if urls.get("official_site"):
        lines.append(f"🌐 Official: {urls.get('official_site')}")
    if urls.get("presale_url"):
        lines.append(f"🎟️ Presale/Signup: {urls.get('presale_url')}")

    yt_url = urls.get("youtube_url")
    if yt_url:
        yt_subs = _format_stat_number(youtube_stats.get("subscribers"))
        yt_views = _format_stat_number(youtube_stats.get("views"))
        lines.append(f"▶️ YouTube: {yt_url} (Subs: {yt_subs} | Views: {yt_views})")

    sp_url = spotify_stats.get("spotify_url") or urls.get("spotify_url")
    if sp_url:
        sp_followers = _format_stat_number(spotify_stats.get("followers"))
        sp_pop = spotify_stats.get("popularity")
        pop_display = f"{sp_pop}" if sp_pop is not None else "—"
        lines.append(f"🎵 Spotify: {sp_url} (Followers: {sp_followers} | Popularity: {pop_display})")

    tt_url = urls.get("tiktok_url")
    if tt_url:
        tt_followers = urls.get("tiktok_followers") or "—"
        lines.append(f"🎬 TikTok: {tt_url} (Followers: {tt_followers})")

    best_cities = intel.get("best_cities") or []
    if best_cities:
        lines.append("🏙️ Best cities: " + ", ".join(best_cities))

    if events:
        lines.append("🎟️ Top events by sellout probability:")
        for ev in events[:10]:
            lines.append(ev.get("line") or "")

    return "\n".join([line for line in lines if line]).strip()[:1900]

def _get_artist_momentum(artist: str) -> Dict[str, float]:
    if viking_db and hasattr(viking_db, "get_artist_counts_time_aware"):
        try:
            counts = viking_db.get_artist_counts_time_aware(artist)
            return {
                "events_30d": float(counts.get("events_30d", 0.0)),
                "news_30d": float(counts.get("news_30d", 0.0)),
            }
        except Exception:
            return {"events_30d": 0.0, "news_30d": 0.0}
    return {"events_30d": 0.0, "news_30d": 0.0}

def _coerce_capacity(event: Dict[str, Any]) -> Optional[int]:
    for key in ("capacity", "venue_capacity", "seatmap_capacity"):
        value = event.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
        if isinstance(value, str):
            match = re.search(r"\d+", value.replace(",", ""))
            if match:
                try:
                    return int(match.group(0))
                except Exception:
                    continue
    return None

def _build_event_lines(
    artist_score: int,
    presale_url: str,
    events: List[Dict[str, Any]],
    momentum: Dict[str, float],
) -> List[Dict[str, Any]]:
    lines: List[Dict[str, Any]] = []
    recent_events_30d = float(momentum.get("events_30d", 0.0))
    news_30d = float(momentum.get("news_30d", 0.0))
    for ev in events:
        city = ev.get("city") or ""
        city_tier = _city_tier_score(city)
        capacity = _coerce_capacity(ev)
        prob = _sellout_probability(
            artist_score,
            city_tier,
            capacity,
            bool(presale_url),
            recent_events_30d,
            news_30d,
        )
        name = ev.get("name") or "Event"
        date = ev.get("date") or ""
        venue = ev.get("venue") or ""
        url = ev.get("url") or ""
        line = f"• {prob}% — {name} ({date}) {venue} {city}".strip()
        if url:
            line += f" — {url}"
        if presale_url:
            line += f" | Presale: {presale_url}"
        lines.append({"prob": prob, "line": line})
    lines.sort(key=lambda x: x.get("prob", 0), reverse=True)
    return lines

def _entry_last_updated(entry: Dict[str, Any]) -> float:
    candidates = []
    urls = entry.get("urls") if isinstance(entry.get("urls"), dict) else {}
    stats = entry.get("stats") if isinstance(entry.get("stats"), dict) else {}
    computed = entry.get("computed") if isinstance(entry.get("computed"), dict) else {}
    for src in (urls, stats, computed):
        ts = src.get("updated_at")
        if ts:
            candidates.append(float(ts))
    last_used = entry.get("last_used_at")
    if last_used:
        candidates.append(float(last_used))
    return max(candidates) if candidates else 0.0

def _select_artists_for_refresh_sync(max_artists: int) -> List[Tuple[str, str]]:
    cache = load_cache()
    selected: List[str] = []
    if usage_db:
        try:
            recent_keys = usage_db.list_recent_artist_keys(days=7, limit=max_artists)
        except Exception:
            recent_keys = []
        for key in recent_keys:
            key = normalize_artist_key(key)
            if not key or key in selected:
                continue
            selected.append(key)

    if len(selected) < max_artists:
        ranked = []
        for key, entry in cache.items():
            if key in selected:
                continue
            ranked.append((key, _entry_last_updated(entry)))
        ranked.sort(key=lambda x: x[1], reverse=True)
        for key, _ts in ranked:
            selected.append(key)
            if len(selected) >= max_artists:
                break

    pairs: List[Tuple[str, str]] = []
    for key in selected:
        entry = cache.get(key, {}) if isinstance(cache, dict) else {}
        name = (entry.get("artist_name") or "").strip()
        pairs.append((key, name or key))
    return pairs

def _refresh_artist_intel_sync(artist_key: str, artist_name: str, force: bool = False) -> Dict[str, int]:
    cache = load_cache()
    entry = _cache_get_artist(cache, artist_key)
    name = (artist_name or entry.get("artist_name") or artist_key).strip()

    updated_urls = 0
    updated_stats = 0

    urls = _get_cached_urls(entry)
    if force or not urls:
        urls = _discover_profile_links(name)
        entry["urls"] = urls
        updated_urls = 1

    stats = _get_cached_stats(entry)
    if force or not stats:
        youtube_stats = _fetch_youtube_stats(urls.get("youtube_url", ""))
        spotify_stats = _fetch_spotify_stats(name)
        stats = {
            "youtube": youtube_stats,
            "spotify": spotify_stats,
            "updated_at": time.time(),
        }
        entry["stats"] = stats
        updated_stats = 1

    spotify_stats = stats.get("spotify", {}) if isinstance(stats, dict) else {}
    youtube_stats = stats.get("youtube", {}) if isinstance(stats, dict) else {}
    score, stars = _compute_artist_score(name, spotify_stats, youtube_stats)

    entry["computed"] = {
        "score": score,
        "stars": stars,
        "best_cities": _best_cities_for_artist(score),
        "updated_at": time.time(),
    }
    entry["artist_name"] = name
    entry["last_refreshed_at"] = time.time()

    _cache_set_artist(cache, artist_key, entry)
    save_cache(cache)

    return {
        "updated_urls": updated_urls,
        "updated_stats": updated_stats,
        "skipped": 1 if not (updated_urls or updated_stats) else 0,
    }

async def _run_intel_refresh_cycle(force: bool = False) -> Dict[str, Any]:
    max_artists = max(1, INTEL_REFRESH_MAX_ARTISTS)
    pairs = await asyncio.to_thread(_select_artists_for_refresh_sync, max_artists)
    if not pairs:
        return {"artists_total": 0, "updated_urls": 0, "updated_stats": 0, "skipped": 0}

    sem = asyncio.Semaphore(max(1, INTEL_REFRESH_CONCURRENCY))
    totals = {"artists_total": len(pairs), "updated_urls": 0, "updated_stats": 0, "skipped": 0}

    async def _worker(key: str, name: str) -> None:
        async with sem:
            try:
                result = await asyncio.to_thread(_refresh_artist_intel_sync, key, name, force)
                totals["updated_urls"] += result.get("updated_urls", 0)
                totals["updated_stats"] += result.get("updated_stats", 0)
                totals["skipped"] += result.get("skipped", 0)
            except Exception as exc:
                logger.warning("Intel refresh failed for %s: %s", name, exc)

    await asyncio.gather(*[_worker(key, name) for key, name in pairs])
    return totals

def _tour_scan_intel_message(item: Dict[str, Any]) -> Optional[str]:
    title = (item.get("title") or "").strip()
    link = (item.get("link") or "").strip()
    artist = title or "Unknown Artist"
    intel = build_artist_intel(artist)
    if not intel:
        return None
    best_cities = intel.get("best_cities") or []
    urls = intel.get("urls") or {}
    lines = [
        f"🗺️ New tour item: {title}" if title else "🗺️ New tour item",
        link,
        f"⭐ Stars: {intel.get('stars')} | Score: {intel.get('score')}/100",
    ]
    if best_cities:
        lines.append("🏙️ Best cities: " + ", ".join(best_cities[:3]))
    if urls.get("official_site"):
        lines.append(f"🌐 Official: {urls.get('official_site')}")
    if urls.get("presale_url"):
        lines.append(f"🎟️ Presale/Signup: {urls.get('presale_url')}")
    return "\n".join([l for l in lines if l]).strip()

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

async def _send_webhook(
    webhook_url: Optional[str],
    content: Optional[str],
    embeds: Optional[list] = None,
) -> None:
    if not webhook_url:
        return
    if content is None and not embeds:
        return
    try:
        wh = discord.SyncWebhook.from_url(webhook_url)
        kwargs = {"content": content or "", "wait": False}
        if embeds:
            kwargs["embeds"] = embeds
        await asyncio.to_thread(wh.send, **kwargs)
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
                try:
                    items = await asyncio.to_thread(price_monitor.poll_prices_once, post_price_alert)
                except TypeError:
                    items = await asyncio.to_thread(price_monitor.poll_prices_once)
                if items:
                    for item in items or []:
                        await post_price_alert(item)
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

async def intel_refresh_loop() -> None:
    if INTEL_REFRESH_SECONDS <= 0:
        logger.info("Intel refresh disabled (INTEL_REFRESH_SECONDS=%s).", INTEL_REFRESH_SECONDS)
        return
    logger.info(
        "Intel refresh loop started (%ss interval, max=%s, concurrency=%s).",
        INTEL_REFRESH_SECONDS,
        INTEL_REFRESH_MAX_ARTISTS,
        INTEL_REFRESH_CONCURRENCY,
    )
    while True:
        try:
            summary = await _run_intel_refresh_cycle(force=False)
            STATUS["last_intel_refresh_unix"] = time.time()
            STATUS["last_intel_refresh_summary"] = summary
        except Exception as e:
            STATUS["last_error"] = f"intel_refresh_loop: {e}"
            logger.warning("Intel refresh tick failed: %s", e)
        await asyncio.sleep(max(300, INTEL_REFRESH_SECONDS))

def start_tour_scan_monitor() -> None:
    if tour_scan_monitor is None:
        logger.info("tour_scan_monitor not available; skipping.")
        return

    starter = getattr(tour_scan_monitor, "start_background_thread", None) or getattr(tour_scan_monitor, "start_tour_scan_monitor", None)
    if not starter:
        logger.info("tour_scan_monitor present but no start_background_thread()/start_tour_scan_monitor() found; skipping.")
        return

    try:
        starter(post_callback=_tour_scan_intel_message)
        logger.info("Tour scan background thread started.")
    except TypeError:
        starter(
            {
                "post_callback": _tour_scan_intel_message,
            }
        )
        logger.info("Tour scan background thread started.")
    except Exception as e:
        STATUS["last_error"] = f"tour_scan_monitor start: {e}"
        logger.warning("Failed to start tour_scan_monitor: %s", e)

# --------------------
# Slash Commands
# --------------------
@tree.command(name="status", description="Show bot status (uptime, memory, monitors).")
async def status_cmd(interaction: discord.Interaction):
    start_time = time.monotonic()
    ok = False
    try:
        effective_tier = await _get_effective_tier(interaction)
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
            "tiers": {
                "effective": effective_tier,
                "default": DEFAULT_TIER,
                "pro_guilds": len(PRO_GUILD_IDS),
                "admin_users": len(ADMIN_USER_IDS),
            },
            "intel_refresh": {
                "interval_seconds": INTEL_REFRESH_SECONDS,
                "max_artists": INTEL_REFRESH_MAX_ARTISTS,
                "concurrency": INTEL_REFRESH_CONCURRENCY,
                "last_refresh_unix": STATUS.get("last_intel_refresh_unix"),
                "last_summary": STATUS.get("last_intel_refresh_summary"),
            },
            "last_error": STATUS.get("last_error"),
        }
        await interaction.response.send_message(
            f"```json\n{json.dumps(data, indent=2, default=str)}\n```",
            ephemeral=True,
        )
        ok = True
    finally:
        await _record_usage("status", interaction, ok, int((time.monotonic() - start_time) * 1000))

@tree.command(name="health", description="Health JSON dump (config presence + task state).")
async def health_cmd(interaction: discord.Interaction):
    start_time = time.monotonic()
    ok = False
    try:
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
                "INTEL_REFRESH_SECONDS": INTEL_REFRESH_SECONDS,
                "INTEL_REFRESH_MAX_ARTISTS": INTEL_REFRESH_MAX_ARTISTS,
            },
            "tasks": {k: task_state(v) for k, v in _bg_tasks.items()},
            "last_error": STATUS.get("last_error"),
        }
        await interaction.response.send_message(
            f"```json\n{json.dumps(health, indent=2, default=str)}\n```",
            ephemeral=True,
        )
        ok = True
    finally:
        await _record_usage("health", interaction, ok, int((time.monotonic() - start_time) * 1000))

@tree.command(name="debug", description="Quick debug dump (non-sensitive).")
async def debug_cmd(interaction: discord.Interaction):
    start_time = time.monotonic()
    ok = False
    try:
        msg = (
            f"rev `{_git_rev()}` | uptime `{_uptime_seconds()}s` | mem `{_memory_mb():.1f}MB`\n"
            f"IDs: price={PRICE_ALERT_CHANNEL_ID} vf={VERIFIED_FAN_ALERT_CHANNEL_ID} tour={TOUR_SCAN_ALERT_CHANNEL_ID}\n"
            f"Webhooks: vf={bool(VERIFIED_FAN_WEBHOOK_URL)} tour={bool(TOUR_SCAN_WEBHOOK_URL)}\n"
            f"Prefixes: {PRICE_PREFIX} {VERIFIED_FAN_PREFIX} {TOUR_SCAN_PREFIX}\n"
            f"Token: {_redact(DISCORD_TOKEN)}"
        )
        await interaction.response.send_message(msg, ephemeral=True)
        ok = True
    finally:
        await _record_usage("debug", interaction, ok, int((time.monotonic() - start_time) * 1000))

@tree.command(name="news_now", description="Get latest tour news for an artist.")
@app_commands.describe(artist="Artist name")
async def news_now_cmd(interaction: discord.Interaction, artist: str):
    start_time = time.monotonic()
    ok = False
    extra = {"artist": artist, "artist_key": normalize_artist_key(artist)}
    try:
        if not await _require_tier(interaction, "PRO"):
            return
        await interaction.response.defer(thinking=True)
        if not get_tour_news:
            await interaction.followup.send("Tour news agent not available.")
            return
        try:
            text = await asyncio.to_thread(get_tour_news, artist)
            await interaction.followup.send(text[:1900])
            ok = True
        except Exception as e:
            await interaction.followup.send(f"Error: {e}")
    finally:
        await _record_usage("news_now", interaction, ok, int((time.monotonic() - start_time) * 1000), extra)

@tree.command(name="events", description="Search Ticketmaster events for an artist.")
@app_commands.describe(artist="Artist name")
async def events_cmd(interaction: discord.Interaction, artist: str):
    start_time = time.monotonic()
    ok = False
    extra = {"artist": artist, "artist_key": normalize_artist_key(artist)}
    try:
        if not await _require_tier(interaction, "PRO"):
            return
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
            ok = True
        except Exception as e:
            await interaction.followup.send(f"Error: {e}")
    finally:
        await _record_usage("events", interaction, ok, int((time.monotonic() - start_time) * 1000), extra)

@tree.command(name="intel", description="Artist Intel v1.1 (links, stats, routing, sellout odds).")
@app_commands.describe(artist="Artist name")
async def intel_cmd(interaction: discord.Interaction, artist: str):
    start_time = time.monotonic()
    ok = False
    extra = {"artist": artist, "artist_key": normalize_artist_key(artist)}
    try:
        if not await _require_tier(interaction, "PRO"):
            return
        await interaction.response.defer(thinking=True)
        try:
            intel = await asyncio.to_thread(build_artist_intel, artist)
            events: List[Dict[str, Any]] = []
            if search_events_for_artist:
                events = await asyncio.to_thread(search_events_for_artist, artist, 15)
            presale_url = (intel.get("urls") or {}).get("presale_url") or ""
            momentum = _get_artist_momentum(artist)
            event_lines = _build_event_lines(int(intel.get("score") or 0), presale_url, events, momentum)
            message = _format_artist_intel_message(artist, intel, event_lines)
            await interaction.followup.send(message or "No intel available yet.")
            ok = True
        except Exception as e:
            await interaction.followup.send(f"Error: {e}")
    finally:
        await _record_usage("intel", interaction, ok, int((time.monotonic() - start_time) * 1000), extra)

@tree.command(name="eventdetails", description="Get Ticketmaster event details by id.")
@app_commands.describe(event_id="Ticketmaster event id")
async def eventdetails_cmd(interaction: discord.Interaction, event_id: str):
    start_time = time.monotonic()
    ok = False
    extra = {"event_id": event_id}
    try:
        if not await _require_tier(interaction, "PRO"):
            return
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
            ok = True
        except Exception as e:
            await interaction.followup.send(f"Error: {e}")
    finally:
        await _record_usage("eventdetails", interaction, ok, int((time.monotonic() - start_time) * 1000), extra)

@tree.command(name="intel_refresh", description="Force a refresh cycle for artist intel (ADMIN only).")
async def intel_refresh_cmd(interaction: discord.Interaction):
    start_time = time.monotonic()
    ok = False
    try:
        if not await _require_tier(interaction, "ADMIN"):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        summary = await _run_intel_refresh_cycle(force=True)
        STATUS["last_intel_refresh_unix"] = time.time()
        STATUS["last_intel_refresh_summary"] = summary
        msg = (
            "✅ Intel refresh complete.\n"
            f"Artists: {summary.get('artists_total', 0)} | "
            f"URLs updated: {summary.get('updated_urls', 0)} | "
            f"Stats updated: {summary.get('updated_stats', 0)} | "
            f"Skipped: {summary.get('skipped', 0)}"
        )
        await interaction.followup.send(msg, ephemeral=True)
        ok = True
    finally:
        await _record_usage("intel_refresh", interaction, ok, int((time.monotonic() - start_time) * 1000))

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

    t4 = asyncio.create_task(intel_refresh_loop(), name="intel_refresh_loop")
    _task_guard("intel_refresh_loop", t4)

# --------------------
# Entrypoint
# --------------------
def main():
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set.")
    client.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main()
