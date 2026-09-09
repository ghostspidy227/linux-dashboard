"""SQLite-backed metrics history: raw 5s samples for 24h, 60s aggregates for 7d."""
import json
import os
import sqlite3
import threading
import time

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "metrics.db")

_conn = None
_lock = threading.Lock()

RANGES = {"1h": 3600, "24h": 86400, "7d": 7 * 86400}


def _db():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("CREATE TABLE IF NOT EXISTS raw (ts REAL PRIMARY KEY, data TEXT)")
        _conn.execute("CREATE TABLE IF NOT EXISTS minute (ts REAL PRIMARY KEY, data TEXT)")
        _conn.commit()
    return _conn


def insert(snapshot, minute=False):
    ts = snapshot["timestamp"]
    data = json.dumps(snapshot, separators=(",", ":"))
    with _lock:
        table = "minute" if minute else "raw"
        _db().execute(f"INSERT OR REPLACE INTO {table} (ts, data) VALUES (?, ?)", (ts, data))
        _db().commit()


def prune():
    """Keep raw 24h, minute-level 7d."""
    with _lock:
        db = _db()
        db.execute("DELETE FROM raw WHERE ts < ?", (time.time() - 86400,))
        db.execute("DELETE FROM minute WHERE ts < ?", (time.time() - RANGES["7d"],))
        db.commit()


def query(range_str="1h", max_points=300):
    seconds = RANGES.get(range_str, 3600)
    table = "raw" if seconds <= 3600 else "minute"
    with _lock:
        rows = _db().execute(
            f"SELECT data FROM {table} WHERE ts >= ? ORDER BY ts", (time.time() - seconds,)
        ).fetchall()
    points = []
    for (data,) in rows:
        try:
            points.append(json.loads(data))
        except json.JSONDecodeError:
            pass
    step = max(1, len(points) // max_points)
    return points[::step]
