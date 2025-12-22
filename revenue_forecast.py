"""
revenue_forecast.py

Simple revenue forecasting engine for Viking AI.

Core idea:
  expected_gross = capacity * expected_attendance_rate * avg_price

We build three scenarios:
  - conservative
  - base case
  - optimistic
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class RevenueForecastInput:
    capacity: int
    min_price: float
    max_price: float
    demand_score: float  # 0–100 from demand_model or manual estimate


@dataclass
class RevenueScenario:
    name: str
    attendance_rate: float
    avg_price: float
    gross: float


@dataclass
class RevenueForecast:
    conservative: RevenueScenario
    base: RevenueScenario
    optimistic: RevenueScenario


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def estimate_show_gross(
    capacity: int,
    min_price: float,
    max_price: float,
    demand_score: float,
) -> RevenueForecast:
    """
    Estimate show gross based on capacity, price range, and demand score.

    demand_score is assumed to be 0–100.

    Returns a RevenueForecast with three scenarios.
    """
    if capacity <= 0:
        capacity = 1

    demand_norm = _clamp(demand_score / 100.0, 0.0, 1.0)

    # Average price assumptions
    pmin = max(0.0, min_price)
    pmax = max(pmin, max_price)
    base_avg_price = (pmin + pmax) / 2.0

    # Attendance rate curves by scenario
    # Base case is basically demand_norm, with conservative/optimistic around it.
    base_attendance = demand_norm
    cons_attendance = _clamp(base_attendance - 0.15, 0.10, 0.95)
    opt_attendance = _clamp(base_attendance + 0.15, 0.20, 1.05)

    # Avg price per scenario – optimistic skews higher, conservative lower
    cons_avg_price = max(pmin, base_avg_price * 0.9)
    opt_avg_price = min(pmax, base_avg_price * 1.15)

    conservative = RevenueScenario(
        name="Conservative",
        attendance_rate=cons_attendance,
        avg_price=cons_avg_price,
        gross=capacity * cons_attendance * cons_avg_price,
    )

    base = RevenueScenario(
        name="Base case",
        attendance_rate=base_attendance,
        avg_price=base_avg_price,
        gross=capacity * base_attendance * base_avg_price,
    )

    optimistic = RevenueScenario(
        name="Optimistic",
        attendance_rate=opt_attendance,
        avg_price=opt_avg_price,
        gross=capacity * opt_attendance * opt_avg_price,
    )

    return RevenueForecast(
        conservative=conservative,
        base=base,
        optimistic=optimistic,
    )
