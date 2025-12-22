cat > /opt/viking-ai/price_monitor.py <<'PY'
"""
price_monitor.py

Minimal price monitor helpers for Viking AI.

- poll_prices_once(): returns a list[dict] of alerts (or [] if none)

This is intentionally a safe stub (no scraping, no external calls).
"""

from __future__ import annotations
from typing import Any, Dict, List
import os
import logging

logger = logging.getLogger("price_monitor")

PRICE_MONITOR_ENABLED = os.getenv("PRICE_MONITOR_ENABLED", "1").strip() not in ("0", "false", "False", "")

def poll_prices_once() -> List[Dict[str, Any]]:
    """
    One polling cycle.
    Return alerts like:
      [{"title": "...", "message": "..."}]
    """
    if not PRICE_MONITOR_ENABLED:
        return []
    return []
PY

python3 -m py_compile /opt/viking-ai/price_monitor.py && echo "✅ price_monitor.py OK"
