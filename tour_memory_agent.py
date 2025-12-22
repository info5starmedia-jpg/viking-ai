import sqlite3
import time
import logging

logger = logging.getLogger("tour_memory_agent")

def get_db():
    conn = sqlite3.connect("viking_ai.db")
    conn.row_factory = sqlite3.Row
    return conn

def ensure_memory_table():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tour_memory (
            artist TEXT,
            metric TEXT,
            value REAL,
            updated INTEGER
        )
    """)
    conn.commit()
    conn.close()

ensure_memory_table()

def update_memory(artist, metric, value):
    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO tour_memory (artist, metric, value, updated)
        VALUES (?, ?, ?, ?)
    """, (artist, metric, value, int(time.time())))
    conn.commit()
    conn.close()

def get_memory_boost(artist):
    conn = get_db()
    rows = conn.execute("SELECT value FROM tour_memory WHERE artist=?", (artist,)).fetchall()

    if not rows:
        return 10  # default small boost

    avg_val = sum([r["value"] for r in rows]) / len(rows)
    return min(100, int(avg_val))
