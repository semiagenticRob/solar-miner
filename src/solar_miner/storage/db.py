"""SQLite time-series storage for readings and daily summaries."""

import sqlite3
from datetime import datetime
from pathlib import Path


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Create tables if they don't exist and return a connection."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            solar_production_w REAL,
            house_consumption_w REAL,
            consumption_source TEXT,
            net_surplus_w REAL,
            smoothed_surplus_w REAL,
            available_surplus_w REAL,
            miner_alpha_target_w REAL,
            miner_alpha_state TEXT,
            miner_beta_target_w REAL,
            miner_beta_state TEXT,
            safety_action TEXT,
            safety_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_summary (
            date TEXT PRIMARY KEY,
            total_solar_kwh REAL,
            total_house_kwh REAL,
            total_mined_kwh REAL,
            total_exported_kwh REAL,
            est_btc_revenue REAL,
            est_grid_credit_value REAL
        );

        CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(timestamp);
    """)

    return conn


def log_reading(
    conn: sqlite3.Connection,
    solar_w: float,
    house_w: float,
    consumption_source: str,
    net_surplus_w: float,
    smoothed_w: float,
    available_w: float,
    alpha_target_w: float,
    alpha_state: str,
    beta_target_w: float,
    beta_state: str,
    safety_action: str,
    safety_reason: str,
) -> None:
    """Insert a single reading row."""
    conn.execute(
        """INSERT INTO readings (
            timestamp, solar_production_w, house_consumption_w, consumption_source,
            net_surplus_w, smoothed_surplus_w, available_surplus_w,
            miner_alpha_target_w, miner_alpha_state,
            miner_beta_target_w, miner_beta_state,
            safety_action, safety_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now().isoformat(),
            solar_w, house_w, consumption_source,
            net_surplus_w, smoothed_w, available_w,
            alpha_target_w, alpha_state,
            beta_target_w, beta_state,
            safety_action, safety_reason,
        ),
    )
    conn.commit()


def get_today_stats(conn: sqlite3.Connection) -> dict:
    """Quick stats for today: total solar kWh, mined kWh, readings count."""
    today = datetime.now().strftime("%Y-%m-%d")
    row = conn.execute(
        """SELECT
            COUNT(*) as readings,
            AVG(solar_production_w) as avg_solar_w,
            AVG(CASE WHEN miner_alpha_target_w > 0 OR miner_beta_target_w > 0
                 THEN miner_alpha_target_w + miner_beta_target_w ELSE 0 END) as avg_mining_w
        FROM readings
        WHERE timestamp LIKE ?""",
        (f"{today}%",),
    ).fetchone()

    if row is None or row[0] == 0:
        return {"readings": 0, "avg_solar_w": 0, "avg_mining_w": 0}

    return {
        "readings": row[0],
        "avg_solar_w": round(row[1] or 0, 1),
        "avg_mining_w": round(row[2] or 0, 1),
    }
