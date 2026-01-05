"""
Ticketmaster agent (canonical entrypoint).

All code should import Ticketmaster functionality from here.
Legacy modules (like ticketmaster_agent_v2.py) may forward to this file.
"""

from agents.ticketmaster_agent import (
    search_events_for_artist,
    get_event_details,
    fetch_verified_fan_programs,
)

__all__ = ["search_events_for_artist", "get_event_details", "fetch_verified_fan_programs"]
