"""Backend tests for the summary page API."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tests.conftest import insert_reading, utc_minutes_ago, utc_now

NY = ZoneInfo("America/New_York")


def _at_local(year, month, day, hour, minute=0):
    """Return UTC ISO string for a given New-York local wall-clock time."""
    return datetime(year, month, day, hour, minute, tzinfo=NY).astimezone(
        ZoneInfo("UTC")
    ).isoformat(timespec="seconds")


def test_health_still_works(client):
    rv = client.get("/health")
    assert rv.status_code == 200
    assert rv.get_json()["ok"] is True


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
    assert body["now"]["age_seconds"] < 120


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
    insert_reading(temp_db, device="c", ppm=500,
                   ts="2025-01-01T00:00:00+00:00")
    rv = client.get("/api/summary?device=c")
    assert rv.get_json()["today"] is None


def test_series_default_range_is_24h(client, temp_db):
    insert_reading(temp_db, device="c", ppm=500)
    rv = client.get("/api/summary?device=c")
    assert rv.get_json()["range"] == "24h"


def test_series_unknown_range_falls_back_to_24h(client, temp_db):
    insert_reading(temp_db, device="c", ppm=500)
    rv = client.get("/api/summary?device=c&range=bogus")
    assert rv.get_json()["range"] == "24h"


def test_series_only_includes_rows_in_window(client, temp_db):
    base = utc_now()
    for i in range(30):
        ts = (base - timedelta(minutes=i)).isoformat(timespec="seconds")
        insert_reading(temp_db, device="c", ppm=500 + i, ts=ts)
    insert_reading(temp_db, device="c", ppm=9999,
                   ts="2020-01-01T00:00:00+00:00")
    rv = client.get("/api/summary?device=c&range=15m")
    series = rv.get_json()["series"]
    assert all(point[1] != 9999 for point in series)
    assert len(series) <= 60
    assert len(series) >= 1


def test_series_downsamples_to_max_points(client, temp_db):
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


def test_series_uses_max_per_bucket_not_avg(client, temp_db):
    """Peaks must not be averaged away — bucket value should be the MAX in the window."""
    base = utc_now()
    insert_reading(temp_db, device="c", ppm=500,
                   ts=(base - timedelta(seconds=2)).isoformat(timespec="seconds"))
    insert_reading(temp_db, device="c", ppm=2500,
                   ts=(base - timedelta(seconds=1)).isoformat(timespec="seconds"))
    insert_reading(temp_db, device="c", ppm=600,
                   ts=base.isoformat(timespec="seconds"))
    rv = client.get("/api/summary?device=c&range=15m")
    bucket_values = [p[1] for p in rv.get_json()["series"]]
    assert max(bucket_values) == 2500, bucket_values


def test_root_returns_html(client):
    rv = client.get("/")
    assert rv.status_code == 200
    assert rv.mimetype == "text/html"
    assert b"<!doctype html>" in rv.data.lower()


def test_static_chart_js_served(client):
    rv = client.get("/static/chart.min.js")
    assert rv.status_code == 200
    assert b"Chart" in rv.data


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
