# readme_updater.py

"""
Viking AI – README auto-updater.

update_readme() builds a human-readable README.txt summarizing:
  - What Viking AI does
  - Current config (announcement channel)
  - Watched Ticketmaster artists
  - Verified Fan watchlist
  - SEO scan targets

Called from bot.py:
  - on_ready()
  - /setchannel
  - /watch_artist
  - /seo_targets_add
"""

import os
import json
from datetime import datetime
from typing import Any, Dict, List

BASE_DIR = os.path.dirname(__file__)
README_PATH = os.path.join(BASE_DIR, "README.txt")

CONFIG_FILE = os.path.join(BASE_DIR, "viking_config.json")
WATCHLIST_FILE = os.path.join(BASE_DIR, "tm_watchlist.json")
VF_WATCH_FILE = os.path.join(BASE_DIR, "tm_vf_watchlist.json")  # we will support both names
SEO_TARGETS_FILE = os.path.join(BASE_DIR, "seo_targets.json")


def _load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def _get_config() -> Dict[str, Any]:
    return _load_json(CONFIG_FILE, {})


def _get_watchlist() -> List[str]:
    return _load_json(WATCHLIST_FILE, [])


def _get_vf_watchlist() -> List[str]:
    # support either old or new naming convention
    if os.path.exists(VF_WATCH_FILE):
        return _load_json(VF_WATCH_FILE, [])
    # fallback to old name used in some earlier iterations
    alt_path = os.path.join(BASE_DIR, "tm_watchlist_verified.json")
    return _load_json(alt_path, [])


def _get_seo_targets() -> List[str]:
    return _load_json(SEO_TARGETS_FILE, [])


def update_readme() -> None:
    """
    Regenerate README.txt with current Viking AI status.
    """
    cfg = _get_config()
    watchlist = _get_watchlist()
    vf_watch = _get_vf_watchlist()
    seo_targets = _get_seo_targets()

    ann_channel = cfg.get("announcement_channel_id")
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    lines: List[str] = []

    lines.append("Viking AI – Discord Touring, SEO & Media Agent")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Last updated: {timestamp}")
    lines.append("")
    lines.append("Overview")
    lines.append("--------")
    lines.append(
        "Viking AI is a Discord bot that helps monitor Ticketmaster tours, "
        "scan tour news, compute demand ratings, run SEO audits, and create short AI videos."
    )
    lines.append("")
    lines.append("Core features")
    lines.append("-------------")
    lines.append("- Ticketmaster Discovery integration (/events, /eventdetails, /artistinfo, /venuesearch)")
    lines.append("- Auto-watch artists and post new tour legs into a chosen channel")
    lines.append("- Tour news scanning via Tavily (/news_now)")
    lines.append("- Time-aware demand rating (/intel) combining:")
    lines.append("    * Ticketmaster events & tour coverage history")
    lines.append("    * Spotify followers & popularity")
    lines.append("    * YouTube subscribers")
    lines.append("- SEO tools: on-page audit, keyword ideas, backlink prospects, scheduled audits")
    lines.append("- Short AI video generation for prompts (/video)")
    lines.append("")
    lines.append("Configuration")
    lines.append("-------------")
    if ann_channel:
        lines.append(f"- Announcement channel ID: {ann_channel}")
    else:
        lines.append("- Announcement channel ID: not set (use /setchannel in Discord)")

    lines.append("")
    lines.append("Ticketmaster watchlist")
    lines.append("----------------------")
    if watchlist:
        lines.append(f"- Total watched artists: {len(watchlist)}")
        for a in watchlist:
            lines.append(f"  • {a}")
    else:
        lines.append("- No artists are currently in the watchlist. Use /watch_artist to add some.")

    lines.append("")
    lines.append("Verified Fan / presale watchlist")
    lines.append("--------------------------------")
    if vf_watch:
        lines.append(f"- Total VF/presale URLs watched: {len(vf_watch)}")
        for u in vf_watch:
            lines.append(f"  • {u}")
    else:
        lines.append("- No Verified Fan URLs stored. Use /vf_watch to add a page you care about.")

    lines.append("")
    lines.append("SEO scan targets")
    lines.append("----------------")
    if seo_targets:
        lines.append(f"- Total SEO targets: {len(seo_targets)}")
        for u in seo_targets:
            lines.append(f"  • {u}")
    else:
        lines.append("- No SEO URLs configured yet. Use /seo_targets_add to add URLs.")

    lines.append("")
    lines.append("Deployment & environment")
    lines.append("------------------------")
    lines.append("The bot expects the following environment variables in .env:")
    lines.append("- DISCORD_TOKEN")
    lines.append("- TICKETMASTER_API_KEY")
    lines.append("- OPENAI_API_KEY (for GPT-4o-mini or similar)")
    lines.append("- GOOGLE_API_KEY / GOOGLE_CSE_ID (if used for any future news features)")
    lines.append("- SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET")
    lines.append("- YOUTUBE_API_KEY")
    lines.append("- TAVILY_API_KEY")
    lines.append("- CANVA_API_KEY (and optional video providers like Pika / Runway keys)")

    lines.append("")
    lines.append("To update this README automatically, the bot calls update_readme()")
    lines.append("whenever you change configuration via slash commands.")

    text = "\n".join(lines)

    try:
        with open(README_PATH, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        # README failure should not crash bot
        pass


if __name__ == "__main__":
    update_readme()
    print(f"README.txt updated at {README_PATH}")
