# Mobile Summary Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a mobile-first summary page to `co2-collector` showing the latest CO2 reading, today's min/avg/max, and a Chart.js time-series with selectable range (15m/1h/24h/7d/30d).

**Architecture:** Bolt onto the existing Flask app at `~/co2-collector/`. Add two routes (`GET /`, `GET /api/summary`) plus a small `templates/index.html` and a vendored `static/chart.min.js`. SQLite is queried directly with bucketed AVG for downsampling. Page state lives in the URL hash so refreshes preserve it.

**Tech Stack:** Python 3 + Flask 3 + sqlite3 (stdlib) + zoneinfo (stdlib); Chart.js v4 vendored locally; pytest for backend tests.

**Spec:** `docs/superpowers/specs/2026-05-03-mobile-summary-page-design.md`

---

## File Structure

```
~/co2-collector/
├── app.py                       ← MODIFY: add helpers + 2 routes
├── templates/
│   └── index.html               ← CREATE: Jinja page (HTML+CSS+JS inline)
├── static/
│   └── chart.min.js             ← CREATE: vendored Chart.js v4 UMD
├── tests/
│   ├── __init__.py              ← CREATE: empty
│   ├── conftest.py              ← CREATE: pytest fixtures (temp DB, client)
│   └── test_api.py              ← CREATE: tests for /api/summary
├── requirements-dev.txt         ← CREATE: pytest pin
└── docs/superpowers/
    ├── specs/2026-05-03-mobile-summary-page-design.md   (exists)
    └── plans/2026-05-03-mobile-summary-page.md          (this file)
```

**Responsibility split inside `app.py`:**

- `init_db()`, `db()`, `close_db()` — unchanged
- `POST /co2`, `GET /co2/recent`, `GET /health` — unchanged
- New module-level helpers:
  - `LOCAL_TZ` constant — `zoneinfo.ZoneInfo("America/New_York")`
  - `RANGES` dict — maps `"15m"|"1h"|"24h"|"7d"|"30d"` to `(seconds, max_points)`
  - `_distinct_devices(conn)` — list devices ordered by most-recent reading
  - `_latest_for_device(conn, device)` — most-recent row dict (or None)
  - `_today_stats(conn, device)` — min/avg/max over local-day window
  - `_series(conn, device, range_key)` — bucketed AVG list `[[ts, ppm], ...]`
- New routes:
  - `GET /` — `render_template("index.html")`
  - `GET /api/summary` — composes the four helpers into the response shape

`app.py` should land at ~200 lines. If it grows past ~300, the helpers can move to a `summary.py` module — but premature splitting is YAGNI.

**Frontend security note:** the page never uses `innerHTML` for dynamic data. All values from the API (numbers, device names, status labels) are written via `textContent` or DOM-element APIs (`new Option(...)`). Static markup is built into `index.html` once and never re-templated by JS — this avoids XSS even if a malicious POST sneaks HTML into a `device` field.

---

## Task 1: Test infrastructure

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: Install pytest into the existing venv**

```bash
cd ~/co2-collector
./venv/bin/pip install pytest==8.3.4
```

Expected: "Successfully installed pytest-8.3.4 …"

- [ ] **Step 2: Create `requirements-dev.txt`**

```
pytest==8.3.4
```

- [ ] **Step 3: Create `tests/__init__.py`** (empty file)

- [ ] **Step 4: Create `tests/conftest.py`**

```python
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
```

- [ ] **Step 5: Create `tests/test_api.py` with one smoke test**

```python
"""Backend tests for the summary page API."""
from tests.conftest import insert_reading


def test_health_still_works(client):
    rv = client.get("/health")
    assert rv.status_code == 200
    assert rv.get_json()["ok"] is True
```

- [ ] **Step 6: Run the smoke test**

```bash
cd ~/co2-collector
./venv/bin/python -m pytest tests/ -v
```

Expected: `1 passed`.

- [ ] **Step 7: Commit**

```bash
git add requirements-dev.txt tests/
git commit -m "test: add pytest harness with temp-db fixture"
```

---

## Task 2: `/api/summary` — devices list

**Files:**
- Modify: `app.py` (add `/api/summary` route returning only `devices`)
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api.py`:

```python
def test_summary_lists_devices_most_recent_first(client, temp_db):
    insert_reading(temp_db, device="canary-01", ppm=500,
                   ts="2026-05-01T12:00:00+00:00")
    insert_reading(temp_db, device="canary-02", ppm=700,
                   ts="2026-05-03T12:00:00+00:00")  # newer
    rv = client.get("/api/summary")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["devices"] == ["canary-02", "canary-01"]


def test_summary_no_data_returns_empty_devices(client):
    rv = client.get("/api/summary")
    assert rv.status_code == 200
    assert rv.get_json()["devices"] == []
```

- [ ] **Step 2: Run — verify failure**

```bash
./venv/bin/python -m pytest tests/test_api.py::test_summary_lists_devices_most_recent_first -v
```

Expected: FAIL (404, route not registered).

- [ ] **Step 3: Add the route to `app.py`**

After the existing `/health` route, add:

```python
@app.route("/api/summary", methods=["GET"])
def api_summary():
    conn = db()
    rows = conn.execute(
        "SELECT device, MAX(ts) AS last_ts FROM readings"
        " GROUP BY device ORDER BY last_ts DESC"
    ).fetchall()
    devices = [r["device"] for r in rows]
    return jsonify({"devices": devices})
```

- [ ] **Step 4: Run — verify pass**

```bash
./venv/bin/python -m pytest tests/test_api.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api.py
git commit -m "feat: /api/summary returns devices list ordered by recency"
```

---

## Task 3: `/api/summary` — `now` block

**Files:**
- Modify: `app.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_api.py`:

```python
from tests.conftest import utc_minutes_ago


def test_summary_now_returns_latest_reading_for_device(client, temp_db):
    older = utc_minutes_ago(10).isoformat(timespec="seconds")
    newer = utc_minutes_ago(1).isoformat(timespec="seconds")
    insert_reading(temp_db, device="canary-01", ppm=500, ts=older)
    insert_reading(temp_db, device="canary-01", ppm=625,
                   temp_c=22.0, humidity=40.0, ts=newer)
    rv = client.get("/api/summary?device=canary-01")
    body = rv.get_json()
    assert body["device"] == "canary-01"
    assert body["now"]["co2_ppm"] == 625
    assert body["now"]["temp_f"] == 71.6  # 22.0C -> 71.6F
    assert body["now"]["humidity"] == 40.0
    assert body["now"]["age_seconds"] >= 0
    assert body["now"]["age_seconds"] < 120  # generous for slow CI


def test_summary_now_is_none_if_no_data_for_device(client, temp_db):
    insert_reading(temp_db, device="canary-01", ppm=500)
    rv = client.get("/api/summary?device=canary-99")
    body = rv.get_json()
    # When the requested device doesn't exist, fall back to the most recent.
    assert body["device"] == "canary-01"
    assert body["now"] is not None


def test_summary_now_none_when_db_empty(client):
    rv = client.get("/api/summary?device=canary-01")
    body = rv.get_json()
    assert body["device"] is None
    assert body["now"] is None
```

- [ ] **Step 2: Run — verify failure**

```bash
./venv/bin/python -m pytest tests/test_api.py -v
```

Expected: 3 new tests fail (KeyError on missing fields).

- [ ] **Step 3: Implement helpers + extend route**

Replace the `/api/summary` route in `app.py` with:

```python
def _distinct_devices(conn):
    rows = conn.execute(
        "SELECT device, MAX(ts) AS last_ts FROM readings"
        " GROUP BY device ORDER BY last_ts DESC"
    ).fetchall()
    return [r["device"] for r in rows]


def _latest_for_device(conn, device):
    row = conn.execute(
        "SELECT ts, device, co2_ppm, temp_c, humidity, servo_angle"
        " FROM readings WHERE device = ? ORDER BY id DESC LIMIT 1",
        (device,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    if d["temp_c"] is not None:
        d["temp_f"] = round(d["temp_c"] * 9 / 5 + 32, 1)
    # age_seconds: now() - ts
    try:
        ts_dt = datetime.fromisoformat(d["ts"])
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        d["age_seconds"] = max(0, int((datetime.now(timezone.utc) - ts_dt).total_seconds()))
    except ValueError:
        d["age_seconds"] = None
    return d


@app.route("/api/summary", methods=["GET"])
def api_summary():
    conn = db()
    devices = _distinct_devices(conn)
    requested = request.args.get("device")
    device = requested if requested in devices else (devices[0] if devices else None)
    now_block = _latest_for_device(conn, device) if device else None
    return jsonify({
        "device": device,
        "devices": devices,
        "now": now_block,
    })
```

- [ ] **Step 4: Run — verify pass**

```bash
./venv/bin/python -m pytest tests/test_api.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api.py
git commit -m "feat: /api/summary returns latest reading + age_seconds"
```

---

## Task 4: `/api/summary` — `today` block (local timezone)

**Files:**
- Modify: `app.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_api.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def _at_local(year, month, day, hour, minute=0):
    """Return UTC ISO string for a given New-York local wall-clock time."""
    return datetime(year, month, day, hour, minute, tzinfo=NY).astimezone(
        ZoneInfo("UTC")
    ).isoformat(timespec="seconds")


def test_today_stats_window_is_local_day(client, temp_db, monkeypatch):
    # Freeze "now" to 2026-05-03 14:00 New_York.
    fixed_now = datetime(2026, 5, 3, 14, 0, tzinfo=NY)

    import app as app_module

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now

    monkeypatch.setattr(app_module, "datetime", FakeDateTime)

    # Yesterday 23:00 NY  -> NOT included
    insert_reading(temp_db, device="c", ppm=2000,
                   ts=_at_local(2026, 5, 2, 23, 0))
    # Today 02:00 NY      -> included (min)
    insert_reading(temp_db, device="c", ppm=400,
                   ts=_at_local(2026, 5, 3, 2, 0))
    # Today 10:00 NY      -> included
    insert_reading(temp_db, device="c", ppm=600,
                   ts=_at_local(2026, 5, 3, 10, 0))
    # Today 13:00 NY      -> included (max)
    insert_reading(temp_db, device="c", ppm=800,
                   ts=_at_local(2026, 5, 3, 13, 0))

    rv = client.get("/api/summary?device=c")
    today = rv.get_json()["today"]
    assert today["min"] == 400
    assert today["max"] == 800
    assert today["avg"] == 600  # (400+600+800)/3


def test_today_stats_none_when_no_today_rows(client, temp_db):
    # Old row only.
    insert_reading(temp_db, device="c", ppm=500,
                   ts="2025-01-01T00:00:00+00:00")
    rv = client.get("/api/summary?device=c")
    assert rv.get_json()["today"] is None
```

- [ ] **Step 2: Run — verify failure**

```bash
./venv/bin/python -m pytest tests/test_api.py -v
```

Expected: new tests fail (KeyError on `"today"`).

- [ ] **Step 3: Add `LOCAL_TZ`, `_today_stats`, wire into route**

At the top of `app.py` near the other imports:

```python
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/New_York")
```

Add helper:

```python
def _today_stats(conn, device):
    """Min/avg/max for today, where 'today' is the current local-tz day."""
    now_local = datetime.now(LOCAL_TZ)
    local_midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff_utc = local_midnight.astimezone(timezone.utc).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT MIN(co2_ppm) AS lo, MAX(co2_ppm) AS hi, AVG(co2_ppm) AS avg"
        " FROM readings WHERE device = ? AND ts >= ?",
        (device, cutoff_utc),
    ).fetchone()
    if row is None or row["lo"] is None:
        return None
    return {"min": int(row["lo"]), "max": int(row["hi"]),
            "avg": int(round(row["avg"]))}
```

Update the route to include `today`:

```python
    today_block = _today_stats(conn, device) if device else None
    return jsonify({
        "device": device,
        "devices": devices,
        "now": now_block,
        "today": today_block,
    })
```

- [ ] **Step 4: Run — verify pass**

```bash
./venv/bin/python -m pytest tests/test_api.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api.py
git commit -m "feat: /api/summary today block in America/New_York"
```

---

## Task 5: `/api/summary` — `range` + `series` with downsampling

**Files:**
- Modify: `app.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_api.py`:

```python
def test_series_default_range_is_24h(client, temp_db):
    insert_reading(temp_db, device="c", ppm=500)
    rv = client.get("/api/summary?device=c")
    assert rv.get_json()["range"] == "24h"


def test_series_unknown_range_falls_back_to_24h(client, temp_db):
    insert_reading(temp_db, device="c", ppm=500)
    rv = client.get("/api/summary?device=c&range=bogus")
    assert rv.get_json()["range"] == "24h"


def test_series_only_includes_rows_in_window(client, temp_db):
    # 30 rows over 30 minutes, plus one ancient row that must be excluded.
    base = utc_now()
    for i in range(30):
        ts = (base - timedelta(minutes=i)).isoformat(timespec="seconds")
        insert_reading(temp_db, device="c", ppm=500 + i, ts=ts)
    insert_reading(temp_db, device="c", ppm=9999,
                   ts="2020-01-01T00:00:00+00:00")
    rv = client.get("/api/summary?device=c&range=15m")
    series = rv.get_json()["series"]
    assert all(point[1] != 9999 for point in series)
    assert len(series) <= 60  # 15m max_points
    assert len(series) >= 1


def test_series_downsamples_to_max_points(client, temp_db):
    # 1000 rows in the last 24h -> must downsample to <= 288.
    base = utc_now()
    for i in range(1000):
        ts = (base - timedelta(seconds=i * 80)).isoformat(timespec="seconds")
        insert_reading(temp_db, device="c", ppm=500 + (i % 50), ts=ts)
    rv = client.get("/api/summary?device=c&range=24h")
    series = rv.get_json()["series"]
    assert 1 < len(series) <= 288


def test_series_oldest_first(client, temp_db):
    base = utc_now()
    for i in range(5):
        ts = (base - timedelta(minutes=i)).isoformat(timespec="seconds")
        insert_reading(temp_db, device="c", ppm=500 + i, ts=ts)
    rv = client.get("/api/summary?device=c&range=15m")
    series = rv.get_json()["series"]
    timestamps = [p[0] for p in series]
    assert timestamps == sorted(timestamps)
```

Also update the `from tests.conftest` import line at the top of `test_api.py` to include the new helpers:

```python
from datetime import timedelta
from tests.conftest import insert_reading, utc_minutes_ago, utc_now
```

- [ ] **Step 2: Run — verify failure**

```bash
./venv/bin/python -m pytest tests/test_api.py -v
```

Expected: new tests fail.

- [ ] **Step 3: Add `RANGES`, `_series`, wire into route**

Near the top of `app.py`:

```python
RANGES = {
    "15m": (15 * 60,           60),
    "1h":  (60 * 60,           120),
    "24h": (24 * 60 * 60,      288),
    "7d":  (7 * 24 * 60 * 60,  336),
    "30d": (30 * 24 * 60 * 60, 360),
}
DEFAULT_RANGE = "24h"
```

Add helper:

```python
def _series(conn, device, range_key):
    """Bucket-averaged time series, oldest first."""
    window_s, max_points = RANGES[range_key]
    bucket_s = max(1, window_s // max_points)
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_s)) \
        .isoformat(timespec="seconds")
    # Group by integer bucket index based on epoch seconds.
    rows = conn.execute(
        """
        SELECT
            MIN(ts) AS bucket_ts,
            AVG(co2_ppm) AS avg_ppm
        FROM readings
        WHERE device = ? AND ts >= ?
        GROUP BY CAST(strftime('%s', ts) AS INTEGER) / ?
        ORDER BY bucket_ts ASC
        """,
        (device, cutoff, bucket_s),
    ).fetchall()
    return [[r["bucket_ts"], int(round(r["avg_ppm"]))] for r in rows]
```

Update the route to parse range and include series:

```python
@app.route("/api/summary", methods=["GET"])
def api_summary():
    conn = db()
    devices = _distinct_devices(conn)
    requested = request.args.get("device")
    device = requested if requested in devices else (devices[0] if devices else None)

    range_key = request.args.get("range", DEFAULT_RANGE)
    if range_key not in RANGES:
        range_key = DEFAULT_RANGE

    now_block = _latest_for_device(conn, device) if device else None
    today_block = _today_stats(conn, device) if device else None
    series = _series(conn, device, range_key) if device else []

    return jsonify({
        "device": device,
        "devices": devices,
        "range": range_key,
        "now": now_block,
        "today": today_block,
        "series": series,
    })
```

- [ ] **Step 4: Run — verify pass**

```bash
./venv/bin/python -m pytest tests/test_api.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api.py
git commit -m "feat: /api/summary range parsing + bucketed series"
```

---

## Task 6: Vendor Chart.js

**Files:**
- Create: `static/chart.min.js`

- [ ] **Step 1: Download Chart.js v4 UMD**

```bash
cd ~/co2-collector
mkdir -p static
curl -sSL -o static/chart.min.js \
  https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/chart.umd.min.js
ls -la static/chart.min.js
```

Expected: file ~80–110 KB.

- [ ] **Step 2: Sanity check the file**

```bash
head -c 80 static/chart.min.js
```

Expected: starts with `/*!` (Chart.js banner) followed by minified JS.

- [ ] **Step 3: Commit**

```bash
git add static/chart.min.js
git commit -m "vendor: Chart.js v4.4.6 UMD"
```

---

## Task 7: `GET /` route + minimal template

**Files:**
- Modify: `app.py`
- Create: `templates/index.html` (placeholder for now; full markup arrives in Task 8)
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_api.py`:

```python
def test_root_returns_html(client):
    rv = client.get("/")
    assert rv.status_code == 200
    assert rv.mimetype == "text/html"
    assert b"<!doctype html>" in rv.data.lower()


def test_static_chart_js_served(client):
    rv = client.get("/static/chart.min.js")
    assert rv.status_code == 200
    assert b"Chart" in rv.data  # banner mentions Chart.js
```

- [ ] **Step 2: Run — verify failure**

```bash
./venv/bin/python -m pytest tests/test_api.py -v
```

Expected: new tests fail (404).

- [ ] **Step 3: Add the route**

At the top of `app.py`, change the imports to include `render_template`:

```python
from flask import Flask, request, jsonify, g, render_template
```

Add the route:

```python
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")
```

- [ ] **Step 4: Create `templates/index.html` placeholder**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>CO2 Canary</title>
</head>
<body>
  <p>placeholder</p>
</body>
</html>
```

- [ ] **Step 5: Run — verify pass**

```bash
./venv/bin/python -m pytest tests/test_api.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app.py templates/index.html tests/test_api.py
git commit -m "feat: GET / serves placeholder index.html"
```

---

## Task 8: Build the page — HTML structure + CSS

**Files:**
- Modify: `templates/index.html`

This task is visual-only; verify in a browser, no unit test.

- [ ] **Step 1: Replace `templates/index.html` with the full markup + CSS**

Note the structure: every value the JS will mutate has its own dedicated element with stable id and an empty `textContent`. JS only ever assigns to `textContent` or class attributes — never `innerHTML`.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="theme-color" content="#0f172a" />
  <title>CO2 Canary</title>
  <style>
    :root {
      --bg: #0f172a;
      --card: #1e293b;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --border: #334155;
      --green-deep: #15803d;
      --green-light: #4ade80;
      --yellow: #facc15;
      --red: #ef4444;
      --amber: #f59e0b;
    }
    @media (prefers-reduced-motion: reduce) {
      * { animation: none !important; transition: none !important; }
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font: 16px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
            "Helvetica Neue", Arial, sans-serif;
      padding: 16px;
      max-width: 480px;
      margin: 0 auto;
      padding-bottom: env(safe-area-inset-bottom, 16px);
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 16px;
      margin-bottom: 12px;
    }
    .header {
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 12px;
    }
    .header h1 { margin: 0; font-size: 14px; letter-spacing: 0.08em;
                 text-transform: uppercase; color: var(--muted); font-weight: 600; }
    .picker { background: transparent; color: var(--text); border: 1px solid var(--border);
              border-radius: 8px; padding: 4px 8px; font-size: 14px; }

    .now { text-align: center; padding: 24px 16px 20px; }
    .now-pill {
      display: inline-flex; align-items: baseline; gap: 6px;
      padding: 12px 28px; border-radius: 999px;
      font-size: 56px; font-weight: 700; line-height: 1; letter-spacing: -0.02em;
      transition: background-color 200ms ease;
    }
    .now-pill .unit { font-size: 22px; font-weight: 500; opacity: 0.85; }
    .pill-deep   { background: var(--green-deep);  color: #fff; }
    .pill-light  { background: var(--green-light); color: #052e16; }
    .pill-yellow { background: var(--yellow);      color: #422006; }
    .pill-red    { background: var(--red);         color: #fff; }
    .pill-muted  { background: #475569;            color: #cbd5e1; }

    .status { margin-top: 8px; color: var(--muted); display: flex;
              gap: 6px; align-items: center; justify-content: center; }
    .status .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--muted); }
    .env { margin-top: 14px; color: var(--muted); font-size: 15px; }
    .env strong { color: var(--text); font-weight: 600; }
    .updated { margin-top: 6px; font-size: 13px; color: var(--muted); }
    .updated.warn  { color: var(--amber); }
    .updated.stale { color: var(--red); }

    .today { display: flex; justify-content: space-around; }
    .today .stat { text-align: center; }
    .today .stat .label { color: var(--muted); font-size: 12px;
                          letter-spacing: 0.08em; text-transform: uppercase; }
    .today .stat .value { font-size: 22px; font-weight: 600; margin-top: 2px; }

    .ranges { display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap; }
    .ranges button {
      flex: 1; min-width: 56px;
      padding: 8px 0;
      background: transparent; color: var(--muted);
      border: 1px solid var(--border); border-radius: 999px;
      font-size: 14px; cursor: pointer;
    }
    .ranges button.active { background: var(--text); color: var(--bg); border-color: var(--text); }

    .chart-wrap { position: relative; height: 220px; }

    .banner {
      position: fixed; top: 8px; left: 50%; transform: translateX(-50%);
      background: var(--amber); color: #422006; padding: 6px 14px;
      border-radius: 999px; font-size: 13px; font-weight: 600;
      box-shadow: 0 2px 8px rgba(0,0,0,0.3);
      opacity: 0; pointer-events: none; transition: opacity 200ms ease;
    }
    .banner.show { opacity: 1; }
  </style>
</head>
<body>
  <div id="offline-banner" class="banner">offline · retrying</div>

  <div class="card">
    <div class="header">
      <h1>CO2 Canary</h1>
      <select id="device-picker" class="picker" hidden></select>
    </div>
    <div class="now">
      <div id="now-pill" class="now-pill pill-muted">
        <span id="now-value">—</span><span class="unit">ppm</span>
      </div>
      <div class="status">
        <span id="status-dot" class="dot"></span>
        <span id="status-label">no data</span>
      </div>
      <div class="env">
        <strong id="temp">—</strong> °F
        &nbsp;·&nbsp;
        <strong id="humidity">—</strong> % RH
      </div>
      <div id="updated" class="updated">no readings yet</div>
    </div>
  </div>

  <div class="card today">
    <div class="stat"><div class="label">min</div><div id="today-min" class="value">—</div></div>
    <div class="stat"><div class="label">avg</div><div id="today-avg" class="value">—</div></div>
    <div class="stat"><div class="label">max</div><div id="today-max" class="value">—</div></div>
  </div>

  <div class="card">
    <div class="ranges" id="ranges">
      <button data-range="15m">15m</button>
      <button data-range="1h">1h</button>
      <button data-range="24h" class="active">24h</button>
      <button data-range="7d">7d</button>
      <button data-range="30d">30d</button>
    </div>
    <div class="chart-wrap"><canvas id="chart"></canvas></div>
  </div>

  <script src="{{ url_for('static', filename='chart.min.js') }}"></script>
  <script>
    /* JS arrives in Task 9 */
  </script>
</body>
</html>
```

- [ ] **Step 2: Run pytest to confirm nothing broke**

```bash
./venv/bin/python -m pytest tests/ -v
```

Expected: all tests still pass.

- [ ] **Step 3: Visual smoke test in a desktop browser**

Start the dev server in a scratch terminal:

```bash
cd ~/co2-collector
./venv/bin/python app.py
```

Open `http://localhost:5004/` in a desktop browser at 400px-wide window.
You should see: dark card with "—" CO2, "no data" status, three "today" stats showing dashes, range buttons (24h highlighted), empty chart area.

Stop the dev server with Ctrl+C.

- [ ] **Step 4: Commit**

```bash
git add templates/index.html
git commit -m "feat: mobile-first page layout + dark CSS"
```

---

## Task 9: Wire the page — fetch, render, range buttons, picker, hash, refresh

**Files:**
- Modify: `templates/index.html` (replace the trailing `<script>` block)

**Security:** the JS below uses `textContent` and `new Option(...)` exclusively for dynamic data. It never sets `innerHTML`. Device strings come from external POSTs and must be treated as untrusted, even though the page renders inside a trusted LAN.

- [ ] **Step 1: Replace the trailing `<script>` block**

```html
<script>
(() => {
  const COLOR_BANDS = [
    { max: 600,     cls: 'pill-deep',   label: 'fresh'  },
    { max: 1000,    cls: 'pill-light',  label: 'ok'     },
    { max: 1500,    cls: 'pill-yellow', label: 'stuffy' },
    { max: Infinity, cls: 'pill-red',   label: 'bad'    },
  ];
  const PILL_CLASSES = ['pill-deep', 'pill-light', 'pill-yellow', 'pill-red', 'pill-muted'];
  const RANGE_KEYS = ['15m', '1h', '24h', '7d', '30d'];
  const REFRESH_MS = 60_000;

  const $ = (id) => document.getElementById(id);
  const els = {
    pill: $('now-pill'), value: $('now-value'),
    dot: $('status-dot'), statusLabel: $('status-label'),
    temp: $('temp'), humidity: $('humidity'), updated: $('updated'),
    todayMin: $('today-min'), todayAvg: $('today-avg'), todayMax: $('today-max'),
    ranges: $('ranges'), picker: $('device-picker'),
    chartCanvas: $('chart'), banner: $('offline-banner'),
  };

  // ---- URL-hash state ----
  function readHash() {
    const params = new URLSearchParams(location.hash.replace(/^#/, ''));
    const range = RANGE_KEYS.includes(params.get('range')) ? params.get('range') : '24h';
    return { device: params.get('device'), range };
  }
  function writeHash(state) {
    const p = new URLSearchParams();
    if (state.device) p.set('device', state.device);
    p.set('range', state.range);
    history.replaceState(null, '', '#' + p.toString());
  }

  let state = readHash();
  let chart = null;

  // ---- Helpers ----
  function bandFor(ppm) { return COLOR_BANDS.find((b) => ppm < b.max); }

  function setPillClass(cls) {
    PILL_CLASSES.forEach((c) => els.pill.classList.remove(c));
    els.pill.classList.add(cls);
  }

  // ---- Renderers (textContent-only for dynamic data) ----
  function renderNow(now) {
    if (!now) {
      setPillClass('pill-muted');
      els.value.textContent = '—';
      els.statusLabel.textContent = 'no data';
      els.dot.style.background = '';
      els.temp.textContent = '—';
      els.humidity.textContent = '—';
      els.updated.className = 'updated';
      els.updated.textContent = 'no readings yet';
      return;
    }
    const band = bandFor(now.co2_ppm);
    setPillClass(band.cls);
    els.value.textContent = String(now.co2_ppm);
    els.statusLabel.textContent = band.label;
    els.dot.style.background = getComputedStyle(els.pill).backgroundColor;
    els.temp.textContent = (now.temp_f != null) ? now.temp_f.toFixed(1) : '—';
    els.humidity.textContent = (now.humidity != null) ? String(Math.round(now.humidity)) : '—';

    const age = now.age_seconds ?? 0;
    let cls = 'updated', txt;
    if (age < 60) txt = `updated ${age}s ago`;
    else if (age < 3600) txt = `updated ${Math.floor(age / 60)}m ago`;
    else txt = `updated ${Math.floor(age / 3600)}h ago`;
    if (age >= 15 * 60) cls = 'updated stale';
    else if (age >= 5 * 60) cls = 'updated warn';
    els.updated.className = cls;
    els.updated.textContent = txt;
  }

  function renderToday(today) {
    if (!today) {
      els.todayMin.textContent = '—';
      els.todayAvg.textContent = '—';
      els.todayMax.textContent = '—';
      return;
    }
    els.todayMin.textContent = String(today.min);
    els.todayAvg.textContent = String(today.avg);
    els.todayMax.textContent = String(today.max);
  }

  function renderRanges(activeRange) {
    Array.from(els.ranges.children).forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.range === activeRange);
    });
  }

  function renderDevices(devices, current) {
    if (!devices || devices.length <= 1) {
      els.picker.hidden = true;
      return;
    }
    els.picker.hidden = false;
    while (els.picker.firstChild) els.picker.removeChild(els.picker.firstChild);
    for (const d of devices) {
      // new Option safely escapes the text — never use innerHTML here.
      const opt = new Option(d, d, d === current, d === current);
      els.picker.add(opt);
    }
  }

  function renderChart(series) {
    const labels = series.map((p) => new Date(p[0]));
    const data   = series.map((p) => p[1]);
    if (!chart) {
      chart = new Chart(els.chartCanvas, {
        type: 'line',
        data: { labels, datasets: [{
          data, label: 'ppm',
          borderColor: '#4ade80', backgroundColor: 'rgba(74,222,128,0.15)',
          borderWidth: 2, fill: true, tension: 0.25,
          pointRadius: 0, pointHoverRadius: 4,
        }]},
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { type: 'time', time: { tooltipFormat: 'PP p' },
                 ticks: { color: '#94a3b8', maxRotation: 0, autoSkipPadding: 16 },
                 grid:  { color: '#334155' } },
            y: { ticks: { color: '#94a3b8' }, grid: { color: '#334155' },
                 suggestedMin: 400 },
          },
        },
      });
      // 1500 ppm guide-line
      Chart.register({
        id: 'guideLine',
        afterDatasetsDraw(c) {
          const y = c.scales.y.getPixelForValue(1500);
          if (y < c.chartArea.top || y > c.chartArea.bottom) return;
          const ctx = c.ctx;
          ctx.save();
          ctx.strokeStyle = 'rgba(239, 68, 68, 0.5)';
          ctx.setLineDash([4, 4]);
          ctx.beginPath();
          ctx.moveTo(c.chartArea.left, y);
          ctx.lineTo(c.chartArea.right, y);
          ctx.stroke();
          ctx.restore();
        },
      });
    } else {
      chart.data.labels = labels;
      chart.data.datasets[0].data = data;
      chart.update('none');
    }
  }

  function showBanner(show) { els.banner.classList.toggle('show', show); }

  // ---- Fetch ----
  async function fetchSummary() {
    const params = new URLSearchParams({ range: state.range });
    if (state.device) params.set('device', state.device);
    const r = await fetch('/api/summary?' + params.toString(), { cache: 'no-store' });
    if (!r.ok) throw new Error('http ' + r.status);
    return r.json();
  }

  async function refresh() {
    try {
      const data = await fetchSummary();
      // If server resolved to a different device (e.g. fallback), persist it.
      if (data.device && state.device !== data.device) {
        state.device = data.device;
        writeHash(state);
      }
      renderDevices(data.devices, data.device);
      renderNow(data.now);
      renderToday(data.today);
      renderChart(data.series || []);
      renderRanges(data.range);
      showBanner(false);
    } catch (err) {
      showBanner(true);
    }
  }

  // ---- Event wiring ----
  els.ranges.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-range]');
    if (!btn) return;
    state.range = btn.dataset.range;
    writeHash(state);
    refresh();
  });
  els.picker.addEventListener('change', (e) => {
    state.device = e.target.value;
    writeHash(state);
    refresh();
  });
  window.addEventListener('hashchange', () => {
    state = readHash();
    refresh();
  });

  // ---- Boot ----
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
</script>
```

- [ ] **Step 2: Run pytest — confirm backend tests still pass**

```bash
./venv/bin/python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 3: Run dev server, verify in desktop browser**

```bash
cd ~/co2-collector
./venv/bin/python app.py
```

In a desktop browser at 400px width:

1. Open `http://localhost:5004/`. The current canary reading should show within 1s.
2. The big number's pill should be light green (e.g., 582 ppm is in the 600–1000 band — light green).
3. The "today" row should show three integers.
4. Default chart is 24h; click `7d` → URL hash updates, chart redraws.
5. Reload the page with the hash present → 7d stays selected.
6. Open DevTools → Network → confirm `/api/summary` is hit again ~60s after initial load.
7. DevTools → Application → Toggle "Offline" → on the next refresh the `offline · retrying` banner appears. Restore network → banner disappears within 60s.

Stop dev server with Ctrl+C.

- [ ] **Step 4: Commit**

```bash
git add templates/index.html
git commit -m "feat: wire fetch + render + chart + auto-refresh"
```

---

## Task 10: Restart service and verify on a phone

**Files:** none modified.

- [ ] **Step 1: Restart the systemd service**

```bash
sudo systemctl restart co2-collector
sleep 2
systemctl status co2-collector --no-pager | head -10
```

Expected: `active (running)`, port 5004 listening, no traceback.

- [ ] **Step 2: Verify the new endpoints are live**

```bash
curl -s http://localhost:5004/api/summary | python3 -m json.tool | head -25
curl -sI http://localhost:5004/ | head -3
curl -sI http://localhost:5004/static/chart.min.js | head -3
```

Expected: JSON with `device`, `devices`, `range`, `now`, `today`, `series` keys; HTML 200; JS 200.

- [ ] **Step 3: Phone smoke test**

Find the homelab's current LAN IP (do not hardcode it):

```bash
hostname -I
```

On a phone on the same wifi, open `http://<that-ip>:5004/`.

Verify:
- Page is centered, fits within phone width with no horizontal scroll.
- Big number is readable, color band matches current ppm.
- Tapping range buttons updates the chart smoothly (no full reload).
- Pulling down to refresh the page preserves the selected range and device.
- Wait ~70s on the page; the "updated Ns ago" counter resets when a new reading lands.

- [ ] **Step 4: Tag the deploy in git**

```bash
git tag -a v0.1.0-mobile-page -m "mobile summary page live"
git log --oneline -10
```

---

## Self-review notes (recorded after writing)

- **Spec coverage:** every section in the spec maps to a task —
  - architecture/files → Tasks 1, 7, 8, 9
  - `GET /` → Task 7
  - `/api/summary` shape → Tasks 2–5
  - color bands → Task 9 (`COLOR_BANDS`)
  - range buckets/downsampling → Task 5
  - URL hash + auto-refresh + offline banner → Task 9
  - testing → Tasks 1–7 (pytest), Tasks 8/9/10 (manual phone verification)
- **Type/name consistency:** `_distinct_devices`, `_latest_for_device`, `_today_stats`, `_series` are referenced consistently across Tasks 2–5. JSON keys (`device`, `devices`, `range`, `now`, `today`, `series`) match between server tests, server impl, and template JS.
- **Security:** dynamic data is only ever written via `textContent` or `new Option(...)`. No `innerHTML` anywhere. Pill class changes use `classList.add/remove` against a fixed allowlist (`PILL_CLASSES`). `device` query param is treated as untrusted on the server (looked up against `_distinct_devices` before use).
- **No placeholders:** every step has the literal code or command.
