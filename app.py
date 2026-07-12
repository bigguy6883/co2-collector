"""
Tiny CO2 collector — receives JSON readings from canary devices and stores in SQLite.
Run on homelab:5004.
"""
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify, g, render_template

LOCAL_TZ = ZoneInfo("America/New_York")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "co2.db")

RANGES = {
    "15m": (15 * 60,           60),
    "1h":  (60 * 60,           120),
    "24h": (24 * 60 * 60,      288),
    "7d":  (7 * 24 * 60 * 60,  336),
    "30d": (30 * 24 * 60 * 60, 360),
}
DEFAULT_RANGE = "24h"

app = Flask(__name__)


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                device TEXT NOT NULL,
                co2_ppm INTEGER NOT NULL,
                temp_c REAL,
                humidity REAL,
                servo_angle REAL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_readings_device_ts"
                  " ON readings(device, ts)")
        c.execute("DROP INDEX IF EXISTS idx_readings_device")


@app.route("/co2", methods=["POST"])
def post_co2():
    data = request.get_json(silent=True) or {}
    try:
        co2 = int(data["co2_ppm"])
    except (KeyError, TypeError, ValueError):
        return jsonify(error="co2_ppm required (int)"), 400

    device = str(data.get("device", "canary"))[:64]
    temp = data.get("temp_c")
    hum = data.get("humidity")
    angle = data.get("servo_angle")
    ts_raw = data.get("ts") or None
    if ts_raw is None:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    else:
        try:
            ts_dt = datetime.fromisoformat(str(ts_raw))
        except ValueError:
            return jsonify(error="ts must be ISO-8601"), 400
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        ts = ts_dt.astimezone(timezone.utc).isoformat(timespec="seconds")

    db().execute(
        "INSERT INTO readings (ts, device, co2_ppm, temp_c, humidity, servo_angle)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (ts, device, co2, temp, hum, angle),
    )
    db().commit()
    return jsonify(ok=True, ts=ts, co2_ppm=co2)


@app.route("/co2/recent", methods=["GET"])
def recent():
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        return jsonify(error="limit must be an integer"), 400
    if limit < 1:
        return jsonify(error="limit must be >= 1"), 400
    limit = min(limit, 500)
    rows = db().execute(
        "SELECT ts, device, co2_ppm, temp_c, humidity, servo_angle"
        " FROM readings ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("temp_c") is not None:
            d["temp_f"] = round(d["temp_c"] * 9 / 5 + 32, 2)
        out.append(d)
    return jsonify(out)


@app.route("/health", methods=["GET"])
def health():
    count = db().execute("SELECT COUNT(*) AS n FROM readings").fetchone()["n"]
    return jsonify(ok=True, readings=count)


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
    try:
        ts_dt = datetime.fromisoformat(d["ts"])
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        d["age_seconds"] = max(0, int((datetime.now(timezone.utc) - ts_dt).total_seconds()))
    except ValueError:
        d["age_seconds"] = None
    return d


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


def _series(conn, device, range_key):
    # Use MAX per bucket so transient spikes (which matter for air quality)
    # are never smoothed out by averaging adjacent quiet samples.
    window_s, max_points = RANGES[range_key]
    bucket_s = max(1, window_s // max_points)
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_s)) \
        .isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT
            MIN(ts) AS bucket_ts,
            MAX(co2_ppm) AS peak_ppm
        FROM readings
        WHERE device = ? AND ts >= ?
        GROUP BY CAST(strftime('%s', ts) AS INTEGER) / ?
        ORDER BY bucket_ts ASC
        """,
        (device, cutoff, bucket_s),
    ).fetchall()
    return [[r["bucket_ts"], int(r["peak_ppm"])] for r in rows]


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


if __name__ == "__main__":
    from waitress import serve
    init_db()
    serve(app, host="0.0.0.0", port=5004, threads=4)
