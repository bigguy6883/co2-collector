# Collector Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the five collector issues from the 2026-07-12 code review: 500-on-bad-`limit`, unvalidated client `ts`, missing composite index, Flask dev server in production, and the chart guide line missing on first render.

**Architecture:** All backend changes are inside the single-file Flask app (`app.py`), TDD'd against the existing pytest suite. The frontend fix is a code-move inside `templates/index.html`. The production-server fix swaps `app.run()` for `waitress.serve()` inside `__main__` so the systemd unit (`ExecStart=.../python app.py`) needs no change and `init_db()` still runs at startup — which is also what applies the new index to the live DB on the final restart.

**Tech Stack:** Python 3.13, Flask, SQLite, pytest (fixtures in `tests/conftest.py`: `client`, `temp_db`, helper `insert_reading`), waitress (new), Chart.js (vendored in `static/`).

## Global Constraints

- Repo: `/home/pi/co2-collector`, branch `main`, working tree clean at start.
- The live service `co2-collector.service` (port 5004) is the only always-on homelab service — never kill the port; restart only via `sudo systemctl restart co2-collector`, and only in Task 5 (the single restart that picks up all changes).
- The live DB is `/home/pi/co2-collector/co2.db` (~94k rows). Do not delete or rewrite it; the only schema change is index create/drop via `init_db()`.
- Run tests with: `/home/pi/co2-collector/venv/bin/python -m pytest tests/ -v` (always this venv, never system python).
- Surgical changes only: do not reformat, rename, or "improve" untouched code.
- Out of scope (documented decision): data retention/pruning (review issue #10 — informational only, ~55 MB/yr growth is fine); dropping the pre-existing `idx_readings_ts` index (unused but pre-existing — leave it).

---

### Task 1: Validate `limit` on `/co2/recent`

Fixes review issue #6: `?limit=abc` currently raises `ValueError` → HTTP 500, and `?limit=-1` becomes a negative SQLite LIMIT which returns **all** rows.

**Files:**
- Modify: `app.py:86-99` (the `recent()` view)
- Test: `tests/test_api.py` (append)

**Interfaces:**
- Produces: `GET /co2/recent` returns 400 (JSON `{"error": ...}`) for non-integer or `< 1` limit; caps at 500. No other behavior changes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_recent_limit_non_numeric_returns_400(client):
    rv = client.get("/co2/recent?limit=abc")
    assert rv.status_code == 400
    assert "error" in rv.get_json()


def test_recent_limit_below_one_returns_400(client):
    assert client.get("/co2/recent?limit=0").status_code == 400
    assert client.get("/co2/recent?limit=-1").status_code == 400


def test_recent_limit_capped_not_erroring(client, temp_db):
    insert_reading(temp_db, ppm=500)
    rv = client.get("/co2/recent?limit=99999")
    assert rv.status_code == 200
    assert len(rv.get_json()) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/pi/co2-collector/venv/bin/python -m pytest tests/test_api.py -v -k "recent_limit"`
Expected: `test_recent_limit_non_numeric_returns_400` and `test_recent_limit_below_one_returns_400` FAIL (a 500, not 400 — the ValueError propagates). `test_recent_limit_capped_not_erroring` may already pass.

- [ ] **Step 3: Implement validation**

In `app.py`, replace the first line of `recent()`:

```python
    limit = min(int(request.args.get("limit", 50)), 500)
```

with:

```python
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        return jsonify(error="limit must be an integer"), 400
    if limit < 1:
        return jsonify(error="limit must be >= 1"), 400
    limit = min(limit, 500)
```

- [ ] **Step 4: Run the full suite**

Run: `/home/pi/co2-collector/venv/bin/python -m pytest tests/ -v`
Expected: all tests PASS (every pre-existing test plus the 3 new ones).

- [ ] **Step 5: Commit**

```bash
cd /home/pi/co2-collector
git add app.py tests/test_api.py
git commit -m "fix: return 400 for non-integer or sub-1 limit on /co2/recent"
```

---

### Task 2: Validate and normalize client-supplied `ts` on `POST /co2`

Fixes review issue #7: a malformed `ts` string is stored raw, silently breaking the string-comparison range filters (`ts >= cutoff`) and the `strftime` bucketing in `_series`.

**Files:**
- Modify: `app.py:63-83` (the `post_co2()` view)
- Test: `tests/test_api.py` (append)

**Interfaces:**
- Produces: `POST /co2` returns 400 for unparseable `ts`; parseable `ts` is normalized to UTC `isoformat(timespec="seconds")` (naive input assumed UTC) so every stored row keeps the exact format `YYYY-MM-DDTHH:MM:SS+00:00`. Omitted/empty `ts` still defaults to server UTC now (unchanged).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_post_co2_rejects_malformed_ts(client):
    rv = client.post("/co2", json={"co2_ppm": 500, "ts": "not-a-date"})
    assert rv.status_code == 400
    assert "error" in rv.get_json()


def test_post_co2_normalizes_offset_ts_to_utc(client, temp_db):
    rv = client.post("/co2", json={"co2_ppm": 500,
                                   "ts": "2026-07-12T08:00:00-04:00"})
    assert rv.status_code == 200
    assert rv.get_json()["ts"] == "2026-07-12T12:00:00+00:00"


def test_post_co2_naive_ts_assumed_utc(client, temp_db):
    rv = client.post("/co2", json={"co2_ppm": 500,
                                   "ts": "2026-07-12T12:00:00"})
    assert rv.status_code == 200
    assert rv.get_json()["ts"] == "2026-07-12T12:00:00+00:00"


def test_post_co2_missing_ts_still_defaults_to_now(client, temp_db):
    rv = client.post("/co2", json={"co2_ppm": 500})
    assert rv.status_code == 200
    assert rv.get_json()["ts"].endswith("+00:00")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/pi/co2-collector/venv/bin/python -m pytest tests/test_api.py -v -k "post_co2"`
Expected: `rejects_malformed_ts` FAILS (200, raw string stored), `normalizes_offset_ts` FAILS (ts echoed back unnormalized as `...-04:00`). The naive and missing-ts tests may already pass.

- [ ] **Step 3: Implement validation**

In `app.py` `post_co2()`, replace:

```python
    ts = data.get("ts") or datetime.now(timezone.utc).isoformat(timespec="seconds")
```

with:

```python
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
```

- [ ] **Step 4: Run the full suite**

Run: `/home/pi/co2-collector/venv/bin/python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/pi/co2-collector
git add app.py tests/test_api.py
git commit -m "fix: validate client ts and normalize to UTC on POST /co2"
```

---

### Task 3: Composite `(device, ts)` index

Fixes review issue #8: hot queries filter `device = ? AND ts >= ?` but only single-column indexes exist. Adds `idx_readings_device_ts` and drops the now-redundant `idx_readings_device` (its prefix is covered by the composite). `idx_readings_ts` is left alone (pre-existing).

**Files:**
- Modify: `app.py:46-60` (`init_db()`)
- Test: `tests/test_api.py` (append)

**Interfaces:**
- Produces: `init_db()` is idempotent and leaves exactly these indexes: `idx_readings_ts`, `idx_readings_device_ts` (plus SQLite's internal autoindexes). The live DB picks this up in Task 5's restart.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api.py` (needs `sqlite3` — add `import sqlite3` to the imports at the top of the file):

```python
def test_init_db_index_shape(temp_db):
    with sqlite3.connect(temp_db) as c:
        names = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
            " AND name LIKE 'idx_%'")}
    assert names == {"idx_readings_ts", "idx_readings_device_ts"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/pi/co2-collector/venv/bin/python -m pytest tests/test_api.py::test_init_db_index_shape -v`
Expected: FAIL — actual set is `{"idx_readings_ts", "idx_readings_device"}`.

- [ ] **Step 3: Implement index change**

In `app.py` `init_db()`, replace:

```python
        c.execute("CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_readings_device ON readings(device)")
```

with:

```python
        c.execute("CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_readings_device_ts"
                  " ON readings(device, ts)")
        c.execute("DROP INDEX IF EXISTS idx_readings_device")
```

- [ ] **Step 4: Run the full suite**

Run: `/home/pi/co2-collector/venv/bin/python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/pi/co2-collector
git add app.py tests/test_api.py
git commit -m "perf: composite (device, ts) index; drop redundant device index"
```

---

### Task 4: Chart guide line on first render

Fixes review issue #9: `Chart.register({id: 'guideLine', ...})` runs **after** `new Chart(...)` inside the `if (!chart)` branch, so the 1500 ppm dashed red line is missing until the next repaint (up to 60 s). Move the registration to script-init time, before any chart exists.

**Files:**
- Modify: `templates/index.html` (the `renderChart` function, ~lines 258-303, and the state declarations ~line 184)

**Interfaces:**
- Produces: no API change; `renderChart(series)` behavior identical except the guide line draws on the very first render.

- [ ] **Step 1: Move the plugin registration**

In `templates/index.html`, delete this entire block from inside `renderChart`'s `if (!chart)` branch (it currently sits right after the `new Chart(...)` call):

```javascript
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
```

and insert the identical block at script scope, immediately after the line `let chart = null;` (unindented to match that scope, i.e. 4-space base indent):

```javascript
    let chart = null;

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
```

- [ ] **Step 2: Verify ordering statically**

Run: `grep -n "Chart.register\|new Chart" /home/pi/co2-collector/templates/index.html`
Expected: the `Chart.register` line number is **lower** than the `new Chart` line number, and each appears exactly once.

- [ ] **Step 3: Run the test suite (regression check)**

Run: `/home/pi/co2-collector/venv/bin/python -m pytest tests/ -v`
Expected: all PASS (`test_root_returns_html` still serves the page).

Live visual verification happens after Task 5's restart.

- [ ] **Step 4: Commit**

```bash
cd /home/pi/co2-collector
git add templates/index.html
git commit -m "fix: register chart guide-line plugin before first render"
```

---

### Task 5: Serve with waitress instead of the Flask dev server

Fixes review issue #5. Keeps `ExecStart=.../python app.py` (no systemd edit) by swapping `app.run()` for `waitress.serve()` inside `__main__`, preserving the `init_db()` call — which also applies Task 3's index change to the live DB on this restart.

**Files:**
- Modify: `app.py:200-202` (the `__main__` block)
- Create: `requirements.txt`

**Interfaces:**
- Consumes: `init_db()` from Task 3 (index migration runs here).
- Produces: service listening on `0.0.0.0:5004` under waitress, 4 worker threads.

- [ ] **Step 1: Install and pin waitress**

```bash
/home/pi/co2-collector/venv/bin/pip install waitress
/home/pi/co2-collector/venv/bin/pip show waitress | head -2
```

Create `requirements.txt` with the installed version (substitute the version `pip show` printed):

```
flask
waitress==3.0.2
```

- [ ] **Step 2: Swap the server in `__main__`**

In `app.py`, replace:

```python
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5004)
```

with:

```python
if __name__ == "__main__":
    from waitress import serve
    init_db()
    serve(app, host="0.0.0.0", port=5004, threads=4)
```

- [ ] **Step 3: Run the test suite**

Run: `/home/pi/co2-collector/venv/bin/python -m pytest tests/ -v`
Expected: all PASS (tests never touch `__main__`).

- [ ] **Step 4: Restart the service and verify**

```bash
sudo systemctl restart co2-collector
sleep 2
systemctl is-active co2-collector
curl -s http://localhost:5004/health
sudo journalctl -u co2-collector -n 10 --no-pager
```

Expected: `active`; health returns `{"ok": true, "readings": <n>}`; journal shows **no** "WARNING: This is a development server" line (waitress starts silently — absence of the Werkzeug banner is the signal).

- [ ] **Step 5: Verify the live DB picked up the index migration**

```bash
/home/pi/co2-collector/venv/bin/python -c "
import sqlite3
c = sqlite3.connect('/home/pi/co2-collector/co2.db')
print(sorted(r[0] for r in c.execute(
    \"SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'\")))"
```

Expected: `['idx_readings_device_ts', 'idx_readings_ts']`.

- [ ] **Step 6: Verify end-to-end (dashboard + guide line + fresh data)**

```bash
curl -s "http://localhost:5004/api/summary?range=15m" | head -c 300
```

Expected: JSON with `"device": "canary-01"` and a recent `now` block. If a browser is handy, load `http://homelab.local:5004/` and confirm the dashed red 1500 ppm guide line is visible immediately on first paint (review issue #9's live check).

- [ ] **Step 7: Commit**

```bash
cd /home/pi/co2-collector
git add app.py requirements.txt
git commit -m "feat: serve with waitress instead of Flask dev server"
```
