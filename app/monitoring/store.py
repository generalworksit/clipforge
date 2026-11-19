"""Durable monitor configuration, current state and quota accounting."""
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager, closing
from pathlib import Path
from ..config import ROOT

DATA = ROOT / "monitor-data"
_lock = threading.RLock()


@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True)
    with _lock, closing(sqlite3.connect(DATA / "monitor.sqlite3", timeout=10)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS monitors (id TEXT PRIMARY KEY, config TEXT NOT NULL, runtime TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS quota (at REAL NOT NULL, cost INTEGER NOT NULL)")
        yield connection


def all_monitors():
    with db() as c:
        return [{"id": r["id"], **json.loads(r["config"]), "runtime": json.loads(r["runtime"])} for r in c.execute("SELECT * FROM monitors ORDER BY rowid DESC")]


def save(config, monitor_id=None):
    monitor_id = monitor_id or uuid.uuid4().hex[:12]
    with db() as c:
        c.execute("INSERT INTO monitors VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config",
                  (monitor_id, json.dumps(config), json.dumps({"state": "disabled", "detail": "Not started"})))
    return monitor_id


def runtime(monitor_id, **values):
    values["updated"] = time.time()
    with db() as c:
        c.execute("UPDATE monitors SET runtime=? WHERE id=?", (json.dumps(values), monitor_id))


def remove(monitor_id):
    with db() as c:
        c.execute("DELETE FROM monitors WHERE id=?", (monitor_id,))


def spend(cost):
    # A rolling 24h allowance is conservative across Google's daily reset boundary.
    with db() as c:
        c.execute("DELETE FROM quota WHERE at < ?", (time.time() - 86400,))
        used = c.execute("SELECT COALESCE(SUM(cost),0) FROM quota").fetchone()[0]
        if used + cost > 8500:
            raise RuntimeError("Local YouTube quota budget reached. Monitoring will retry later.")
        c.execute("INSERT INTO quota VALUES (?,?)", (time.time(), cost))


def quota_used():
    with db() as c:
        return c.execute("SELECT COALESCE(SUM(cost),0) FROM quota WHERE at > ?", (time.time()-86400,)).fetchone()[0]
