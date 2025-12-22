"""
arbitrage_agent.py

Ticket arbitrage helper for Viking AI.

Given face (primary) price and resale floor, compute:
  - spread
  - arbitrage factor (resale / face)
  - simple rating text
"""

from dataclasses import dataclass


@dataclass
class ArbitrageAnalysis:
    face_floor: float
    resale_floor: float
    spread: float
    factor: float
    rating: str
    notes: str


def analyze_arbitrage(
    face_floor: float,
    resale_floor: float,
    *,
    primary_fees_pct: float = 0.0,
    resale_fees_pct: float = 0.0,
) -> ArbitrageAnalysis:
    """
    Compute arbitrage opportunity.

    primary_fees_pct and resale_fees_pct are percentages (e.g. 0.15 = 15%)
    to approximate fees on each side.
    """
    if face_floor <= 0 or resale_floor <= 0:
        return ArbitrageAnalysis(
            face_floor=face_floor,
            resale_floor=resale_floor,
            spread=0.0,
            factor=0.0,
            rating="invalid",
            notes="Face or resale floor <= 0; cannot compute arbitrage.",
        )

    eff_face = face_floor * (1 + primary_fees_pct)
    eff_resale = resale_floor * (1 - resale_fees_pct)  # what you roughly net
    spread = eff_resale - eff_face
    factor = eff_resale / eff_face if eff_face > 0 else 0.0

    if factor >= 2.0 and spread >= 50:
        rating = "🔥 strong"
        notes = "Resale is 2x+ face with healthy absolute margin. Very attractive."
    elif factor >= 1.5 and spread >= 25:
        rating = "✅ decent"
        notes = "Resale comfortably above face with usable margin."
    elif factor >= 1.2 and spread >= 10:
        rating = "➖ thin"
        notes = "Some upside, but margin may vanish with fees or price moves."
    else:
        rating = "⚠️ none"
        notes = "Little to no clear arbitrage after fees."

    return ArbitrageAnalysis(
        face_floor=eff_face,
        resale_floor=eff_resale,
        spread=spread,
        factor=factor,
        rating=rating,
        notes=notes,
    )
