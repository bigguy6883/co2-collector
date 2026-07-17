# co2-collector

Tiny Flask app that receives CO₂ readings from [co2-canary](https://github.com/bigguy6883/co2-canary) (or any device that can POST JSON), stores them in SQLite, and serves a phone-friendly dashboard.

## What it does

- **Ingests** readings via `POST /co2` (JSON, no auth — designed for a trusted LAN).
- **Stores** them in a single-file SQLite database (`co2.db`).
- **Serves** a single-page dashboard at `/` with:
  - Current CO₂ as a colored pill (green/yellow/red).
  - Temperature, humidity, and "last updated" age.
  - Today's min / avg / max (in your local timezone).
  - A Chart.js series with selectable ranges (15 m, 1 h, 24 h, 7 d, 30 d).
  - A device picker if multiple canaries are reporting.

The chart uses **MAX-per-bucket** rather than averaging, so transient spikes (which is the whole point of an air-quality monitor) never get smoothed out.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/co2` | Ingest a reading |
| GET  | `/co2/recent?limit=N` | Last N readings (max 500) |
| GET  | `/health` | `{ok, readings}` count |
| GET  | `/api/summary?device=X&range=24h` | Dashboard data (now, today, series) |
| GET  | `/` | Dashboard UI |

### POST /co2

```json
{
  "device": "canary-01",
  "co2_ppm": 742,
  "temp_c": 22.4,
  "humidity": 41.2,
  "servo_angle": 12.3,
  "ts": "2026-05-04T19:00:00+00:00"
}
```

Only `co2_ppm` is required. `ts` defaults to server-side UTC if omitted. `device` defaults to `"canary"`.

## Setup

```bash
git clone https://github.com/bigguy6883/co2-collector.git
cd co2-collector
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py            # serves via waitress on 0.0.0.0:5004
```

The schema is created on first run. No migration step needed.

### Run as a systemd service

The app is intended to run on a small home server. Example unit (adjust paths/user):

```ini
[Unit]
Description=CO2 Collector
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/co2-collector
ExecStart=/home/pi/co2-collector/venv/bin/python /home/pi/co2-collector/app.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now co2-collector
```

## Configuration

A few constants in `app.py`:

- `LOCAL_TZ` — timezone used for the "today" min/avg/max window. Default: `America/New_York`.
- `RANGES` — dashboard time-range presets and bucket counts.
- `DB_PATH` — defaults to `co2.db` next to `app.py`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## License

MIT
