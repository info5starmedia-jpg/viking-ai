"""
Compat shim for older/newer code paths.

Some parts of Viking AI (bot.py) import:
  from ticketmaster_agent_v2 import search_events_for_artist, get_event_details

Your repo currently ships ticketmaster_agent.py, so this module simply re-exports
the needed functions from there (or provides safe fallbacks).
"""

from __future__ import annotations

# Prefer the "real" module you already have
try:
    from ticketmaster_agent import search_events_for_artist, get_event_details  # type: ignore
except Exception:
    # Fallbacks (in case names differ in your repo)
    from ticketmaster_agent import fetch_events_for_artist as search_events_for_artist  # type: ignore
    from ticketmaster_agent import fetch_event_details as get_event_details  # type: ignore

__all__ = ["search_events_for_artist", "get_event_details"]
