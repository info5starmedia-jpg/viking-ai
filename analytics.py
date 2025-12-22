"""
analytics.py - simple command usage tracking for Viking AI.

Stores per-command counts in analytics_store.json so we can show
basic usage stats and export a CSV.
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, Any
import csv
import io
import logging

logger = logging.getLogger(__name__)

ANALYTICS_FILE = "analytics_store.json"


def _load_data() -> Dict[str, Any]:
    if not os.path.exists(ANALYTICS_FILE):
        return {"commands": {}}

    try:
        with open(ANALYTICS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"commands": {}}
        if "commands" not in data or not isinstance(data["commands"], dict):
            data["commands"] = {}
        return data
    except Exception as e:
        logger.error("Error loading analytics file %s: %s", ANALYTICS_FILE, e)
        return {"commands": {}}


def _save_data(data: Dict[str, Any]) -> None:
    try:
        with open(ANALYTICS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error("Error saving analytics file %s: %s", ANALYTICS_FILE, e)


def record_command_usage(command_name: str, user_id: int | None, guild_id: int | None) -> None:
    """
    Increment usage counter for a command. Also keep some light metadata
    (last user, last guild, last timestamp).
    """
    data = _load_data()
    cmds = data.setdefault("commands", {})

    entry = cmds.get(command_name, {})
    count = int(entry.get("count", 0)) + 1
    entry["count"] = count
    entry["last_used_ts"] = int(time.time())
    entry["last_user_id"] = user_id
    entry["last_guild_id"] = guild_id

    cmds[command_name] = entry
    data["commands"] = cmds
    _save_data(data)


def get_usage_summary() -> Dict[str, int]:
    """
    Return a simple {command_name: count} dict for all commands.
    Used by /analytics in bot.py.
    """
    data = _load_data()
    cmds = data.get("commands", {})
    summary: Dict[str, int] = {}
    for name, entry in cmds.items():
        try:
            summary[name] = int(entry.get("count", 0))
        except Exception:
            summary[name] = 0
    return summary


def export_usage_csv() -> bytes:
    """
    Export analytics to CSV as bytes (for Discord file upload).
    Columns: command, count, last_used_ts, last_user_id, last_guild_id
    """
    data = _load_data()
    cmds = data.get("commands", {})

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["command", "count", "last_used_ts", "last_user_id", "last_guild_id"])

    for name, entry in cmds.items():
        writer.writerow(
            [
                name,
                int(entry.get("count", 0)),
                entry.get("last_used_ts", ""),
                entry.get("last_user_id", ""),
                entry.get("last_guild_id", ""),
            ]
        )

    return buf.getvalue().encode("utf-8")
