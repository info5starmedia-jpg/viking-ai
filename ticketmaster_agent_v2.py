"""
Compatibility shim for Ticketmaster (v2).

This file exists ONLY so older code importing
`ticketmaster_agent_v2` continues to work.

The canonical implementation lives in:
    ticketmaster_agent.py
"""

from typing import Any, Dict, List

__all__ = ["search_events_for_artist", "get_event_details", "fetch_verified_fan_programs"]

def search_events_for_artist(artist: str, *args, **kwargs) -> List[Dict[str, Any]]:
    import ticketmaster_agent as tm
    return tm.search_events_for_artist(artist, *args, **kwargs)

def get_event_details(event_id: str, *args, **kwargs) -> Dict[str, Any]:
    import ticketmaster_agent as tm
    return tm.get_event_details(event_id, *args, **kwargs)

def fetch_verified_fan_programs(*args, **kwargs) -> List[Dict[str, Any]]:
    import ticketmaster_agent as tm
    return tm.fetch_verified_fan_programs(*args, **kwargs)
