# db_migrations_demand.py
import sqlite3

DB_PATH = "viking_ai.db"  # change this if your database file has a different name


def apply_event_demand_columns():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    def add_column(sql: str):
        try:
            cur.execute(sql)
        except sqlite3.OperationalError:
            # Column already exists -> ignore the error
            pass

    add_column("ALTER TABLE events ADD COLUMN artist_popularity REAL;")
    add_column("ALTER TABLE events ADD COLUMN city_market_score REAL;")
    add_column("ALTER TABLE events ADD COLUMN historical_sellout_rate REAL;")
    add_column("ALTER TABLE events ADD COLUMN social_buzz_score REAL;")
    add_column("ALTER TABLE events ADD COLUMN streaming_score REAL;")
    add_column("ALTER TABLE events ADD COLUMN days_until_show INTEGER;")
    add_column("ALTER TABLE events ADD COLUMN ticketmaster_demand_bar REAL;")
    add_column("ALTER TABLE events ADD COLUMN presale_phase TEXT;")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    apply_event_demand_columns()
    print("✅ Demand columns applied to events table.")
