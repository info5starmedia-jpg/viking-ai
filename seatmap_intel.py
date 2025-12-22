"""
seatmap_intel.py

Seatmap analytics for Viking AI.

This module does NOT fetch seatmaps itself. Instead, it expects you to pass in
a list of seat dictionaries in the form:

    {
        "section": "101",
        "row": "A",
        "seat": "12",
        "status": "available" or "sold",
        "price": 125.00
    }

You can build this list from Ticketmaster / AXS APIs or scraped data.

The main function assess_event_seatmap() returns a summary dict.
"""

from collections import Counter, defaultdict
from statistics import median


def assess_event_seatmap(seats, price_band_size: float = 25.0) -> dict:
    """
    Analyze a seat list and return:
      - total_seats
      - available_seats
      - sold_seats
      - sell_through_pct
      - price_bands: {band_label: {"count": int, "available": int, "sold": int}}
      - price_stats: {"min": float, "max": float, "median": float}
      - signals: list[str] (simple text observations)
    """
    if not seats:
        return {
            "total_seats": 0,
            "available_seats": 0,
            "sold_seats": 0,
            "sell_through_pct": 0.0,
            "price_bands": {},
            "price_stats": {},
            "signals": ["No seat data provided."],
        }

    total = len(seats)
    available = sum(1 for s in seats if (s.get("status") == "available"))
    sold = sum(1 for s in seats if (s.get("status") == "sold"))
    sell_through = (sold / total * 100.0) if total > 0 else 0.0

    prices = [float(s.get("price", 0.0)) for s in seats if s.get("price") not in (None, "")]
    prices = [p for p in prices if p > 0]
    if prices:
        pmin, pmax, pmed = min(prices), max(prices), median(prices)
    else:
        pmin = pmax = pmed = 0.0

    # Price bands
    bands = defaultdict(lambda: {"count": 0, "available": 0, "sold": 0})
    for s in seats:
        price = float(s.get("price", 0.0) or 0.0)
        if price <= 0:
            band_label = "Unknown"
        else:
            band_floor = price_band_size * int(price // price_band_size)
            band_ceiling = band_floor + price_band_size
            band_label = f"${band_floor:.0f}–${band_ceiling:.0f}"

        bands[band_label]["count"] += 1
        if s.get("status") == "available":
            bands[band_label]["available"] += 1
        elif s.get("status") == "sold":
            bands[band_label]["sold"] += 1

    signals = []

    if sell_through >= 90:
        signals.append("High sell-through (90%+). Very strong demand.")
    elif sell_through >= 70:
        signals.append("Healthy sell-through (70–90%).")
    elif sell_through <= 40:
        signals.append("Low sell-through (<40%). Soft demand or early on-sale.")

    if pmin > 0 and pmax > 0 and pmax >= 3 * pmin:
        signals.append("Wide price spread detected (max >= 3x min). Dynamic pricing strong.")

    # Look for bands that are almost sold out vs. very open
    for label, stats in bands.items():
        c = stats["count"]
        if c == 0:
            continue
        avail = stats["available"]
        sold_in_band = stats["sold"]
        st = sold_in_band / c * 100.0
        if st >= 95 and c >= 20:
            signals.append(f"Price band {label} is ~sold out ({st:.1f}% sell-through).")
        elif st <= 20 and c >= 20:
            signals.append(f"Price band {label} is very open ({st:.1f}% sold).")

    return {
        "total_seats": total,
        "available_seats": available,
        "sold_seats": sold,
        "sell_through_pct": round(sell_through, 2),
        "price_bands": dict(bands),
        "price_stats": {
            "min": pmin,
            "max": pmax,
            "median": pmed,
        },
        "signals": signals,
    }
