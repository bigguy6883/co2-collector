"""
Tiny CO2 collector — receives JSON readings from canary devices and stores in SQLite.
Run on homelab:5004.
"""
import os
import sqlite3
from datetime import datetime, timezone
from flask import Flask, request, jsonify, g

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "co2.db")

app = Flask(__name__)


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
        c.execute("CREATE INDEX IF NOT EXISTS idx_readings_device ON readings(device)")


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
    ts = data.get("ts") or datetime.now(timezone.utc).isoformat(timespec="seconds")

    db().execute(
        "INSERT INTO readings (ts, device, co2_ppm, temp_c, humidity, servo_angle)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (ts, device, co2, temp, hum, angle),
    )
    db().commit()
    return jsonify(ok=True, ts=ts, co2_ppm=co2)


@app.route("/co2/recent", methods=["GET"])
def recent():
    limit = min(int(request.args.get("limit", 50)), 500)
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


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5004)
