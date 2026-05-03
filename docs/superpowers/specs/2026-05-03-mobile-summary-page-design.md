# CO2 Collector — Mobile Summary Page

**Date:** 2026-05-03
**Status:** Design approved, ready for implementation plan

## Goal

Add a small, mobile-first webpage to the existing `co2-collector` Flask app
(homelab:5004) that summarizes CO2 readings at a glance and lets the user
expand or shrink the chart time window. The current `now`-style readout and a
"today" snapshot are both shown on the same page.

## Non-goals

- Authentication. LAN-only access, same trust model as other homelab apps.
- Multi-page nav, settings UI, alerting, historical export.
- Replacing the existing `/co2`, `/co2/recent`, or `/health` endpoints.
- Multi-device dashboards on one screen (one device at a time, picker switches).

## User experience

Open the collector's URL (`http://<homelab-ip>:5004/`) on a phone. See:

1. The current CO2 reading as a large color-coded number, with a status dot
   ("fresh", "ok", "stuffy", "bad").
2. Temp °F + humidity %.
3. "Updated Ns ago" — turns amber after 5 minutes, red after 15.
4. Today's min / avg / max for the selected device, in the homelab's local
   timezone (America/New_York).
5. A line+area chart with five range buttons: `15m · 1h · 24h · 7d · 30d`
   (default 24h). A horizontal guide line at 1500 ppm marks the "bad" band.
6. A device picker (only visible when >1 device has ever reported) to switch
   the page focus.

Page state lives in the URL hash (`#device=canary-01&range=24h`) so refreshes
preserve the view.

The page auto-refreshes every 60 seconds — matched to the canary's ~65 s
posting cadence so we don't re-render with identical data. On fetch failure,
the page keeps the last good data visible and shows a small "offline ·
retrying" banner.

## Color bands (CO2 ppm)

Applied to the big number's background pill and the status dot:

| Range          | Band         | Label  |
|----------------|--------------|--------|
| < 600          | deep green   | fresh  |
| 600 – 1000     | light green  | ok     |
| 1000 – 1500    | yellow       | stuffy |
| ≥ 1500         | red          | bad    |

## Architecture

Bolt onto the existing service. No new process, no new port.

```
~/co2-collector/
├── app.py              ← add 2 routes (GET /, GET /api/summary)
├── templates/
│   └── index.html      ← new (Jinja, mobile-first CSS, Chart.js)
├── static/
│   └── chart.min.js    ← vendored Chart.js v4 UMD (~80KB, downloaded once)
└── co2.db              ← unchanged
```

Chart.js is vendored locally (not loaded from a CDN) so the dashboard works
even if the homelab loses external internet.

## Endpoints

### `GET /`

Returns `templates/index.html`. No query handling server-side; the page reads
its own URL hash for `device` and `range`.

### `GET /api/summary?device=<id>&range=<key>`

Returns a single JSON blob the page consumes:

```json
{
  "device": "canary-01",
  "devices": ["canary-01"],
  "range": "24h",
  "now": {
    "co2_ppm": 582,
    "temp_f": 71.1,
    "humidity": 35.7,
    "ts": "2026-05-03T10:42:56+00:00",
    "age_seconds": 47
  },
  "today": { "min": 489, "max": 731, "avg": 564 },
  "series": [ ["2026-05-03T10:00:00+00:00", 540], ... ]
}
```

Field details:

- `device`: the device the summary is for. If the requested device has no
  rows, the most-recent device is substituted and returned here.
- `devices`: distinct device IDs that have ever reported, ordered by most
  recent reading first. Drives the picker.
- `range`: echoes the resolved range key (defaults to `24h` if missing/bad).
- `now`: the most recent row for the device, with derived `temp_f` and
  `age_seconds` (server time minus row `ts`). `null` if the device has no
  rows.
- `today`: aggregates over rows with `ts` falling within the current local
  day in `America/New_York`. `null` if no rows for today.
- `series`: downsampled time series for the requested range, oldest → newest.

### Existing endpoints (unchanged)

`POST /co2`, `GET /co2/recent`, `GET /health` — left exactly as they are.

## Range windows and downsampling

| Key | Window      | Max points |
|-----|-------------|-----------:|
| 15m | last 15 min |         60 |
| 1h  | last 1 h    |        120 |
| 24h | last 24 h   |        288 |
| 7d  | last 7 d    |        336 |
| 30d | last 30 d   |        360 |

Downsampling is a SQL bucketed average: each bucket spans
`window_seconds / max_points` and emits `(bucket_start_ts, AVG(co2_ppm))`.
Buckets with no rows are simply absent from the series.

This keeps Chart.js fast on a phone (~43k raw rows in 30 days → 360 points)
while preserving overall shape.

## Data flow

1. Page loads → JS reads hash → `GET /api/summary?device=…&range=…`.
2. Server queries SQLite:
   - `devices`: `SELECT device, MAX(ts) FROM readings GROUP BY device ORDER BY MAX(ts) DESC`.
   - `now`: `SELECT … FROM readings WHERE device=? ORDER BY id DESC LIMIT 1`.
   - `today`: `SELECT MIN, AVG, MAX FROM readings WHERE device=? AND ts >= <local-midnight-utc>`.
   - `series`: bucketed select over the window.
3. Page renders the big number, today stats, and chart.
4. Every 60 s, repeat. On range/device change, repeat immediately and update
   the URL hash.

## Error handling

- **Bad/missing query params:** server falls back to the most-recent device
  and `range=24h`; never errors.
- **Unknown range key:** falls back to `24h`.
- **Device with no rows:** `now`, `today`, `series` are returned as `null` /
  empty array; the page shows "no data yet for this device" in place of the
  number.
- **Fetch failure on the client:** keep last good data visible, show a small
  "offline · retrying" banner, retry on the next 60 s tick.
- **Server exception:** standard Flask 500; client surfaces "couldn't load"
  banner.

## Testing

Manual on a phone over LAN — golden path + edge cases:

- Loads cleanly on iOS Safari and Android Chrome at 360–420px width.
- All five range buttons re-render the chart with sensible point counts.
- URL hash round-trips: load page with `#device=canary-01&range=7d`, see 7d
  selected.
- Auto-refresh: number updates within ~60 s of a new POST.
- Stale staging: stop the canary, watch "updated N ago" cross 5 min (amber)
  and 15 min (red).
- Network blip: pull wifi, see "offline · retrying"; restore wifi, banner
  clears on next tick.

A handful of `curl` checks against `/api/summary` cover the server side:

- `?range=24h` returns a series with ≤ 288 points.
- `?range=bogus` falls back to 24h.
- `?device=does-not-exist` returns `null` for `now`/`today` and `[]` for
  `series`.

## Open questions

None — all design decisions captured above.
