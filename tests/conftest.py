"""Pytest fixtures — gives every test a fresh, isolated SQLite DB."""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

# Make the parent dir importable so `import app` works.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def temp_db(monkeypatch):
    """Point app.DB_PATH at a fresh temp file for the duration of one test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    import app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", path)
    app_module.init_db()
    yield path
    os.remove(path)


@pytest.fixture
def client(temp_db):
    """Flask test client backed by the temp DB."""
    import app as app_module
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def insert_reading(db_path, *, device="canary-01", ppm=600, temp_c=21.5,
                   humidity=40.0, servo_angle=0.0, ts=None):
    """Helper to seed a row at a given UTC timestamp (defaults to now)."""
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    elif isinstance(ts, datetime):
        ts = ts.isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO readings (ts, device, co2_ppm, temp_c, humidity, servo_angle)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ts, device, ppm, temp_c, humidity, servo_angle),
        )


def utc_now():
    return datetime.now(timezone.utc)


def utc_minutes_ago(n):
    return utc_now() - timedelta(minutes=n)
