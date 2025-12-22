# viking_config.py
"""
Global configuration flags for Viking AI.
These allow us to enable/disable heavy or expensive modules safely.
"""

CONFIG = {
    # --- YouTube ---
    "YOUTUBE_HEAVY_MODE": False,   # Off per your choice (no comment scraping)
    "YOUTUBE_CACHE_MINUTES": 60,   # Cache profiles & videos for 1h
    "YOUTUBE_TIMEOUT": 10,         # API timeout in seconds

    # --- Spotify ---
    "SPOTIFY_CACHE_MINUTES": 60,

    # --- Seatmap / Browserless ---
    "SEATMAP_ENABLED": False,      # Off until you confirm Browserless key
    "SEATMAP_TIMEOUT": 12,

    # --- Touring Brain ---
    "ENABLE_MARKET_HEAT": True,
    "ENABLE_SPOTIFY": True,
    "ENABLE_YOUTUBE": True,

    # --- Logging ---
    "LOG_LEVEL": "INFO",

    # --- Discord ---
    "EPHEMERAL_RESPONSES": False
}

def get(key: str, default=None):
    return CONFIG.get(key, default)
