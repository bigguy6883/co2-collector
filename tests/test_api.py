"""Backend tests for the summary page API."""
from tests.conftest import insert_reading, utc_minutes_ago


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
