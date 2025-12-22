# agents/seats_agent.py
# ------------------------------------------------------------
# Seatmap Heat Agent
# Converts Ticketmaster seatmap JSON to demand zones
# ------------------------------------------------------------

from typing import Dict, Any, List


async def analyze_seatmap(seatmap: Dict[str, Any]) -> Dict[str, Any]:
    """
    Processes TM seatmap data into demand heat zones.
    A simplified heuristic — upgrade with real data later.
    """

    zones = []
    arbitrage = []

    for section in seatmap.get("sections", []):
        zone_name = section.get("name", "Unknown")
        price = section.get("price", 0)
        available = section.get("available", 0)

        # Heat = price divided by availability (simple heuristic)
        heat = 0
        if available > 0:
            heat = round((price / max(available, 1)) * 10, 2)

        zone_data = {
            "zone": zone_name,
            "price": price,
            "available": available,
            "heat": heat,
        }
        zones.append(zone_data)

        # Arbitrage if high heat but low price
        if heat > 50 and price < 120:
            arbitrage.append(zone_data)

    # Sort by heat descending
    zones = sorted(zones, key=lambda z: z["heat"], reverse=True)

    return {
        "zones": zones,
        "arbitrage_opportunities": arbitrage,
    }
