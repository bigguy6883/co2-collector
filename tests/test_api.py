"""Backend tests for the summary page API."""
from tests.conftest import insert_reading


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
