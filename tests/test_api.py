"""Backend tests for the summary page API."""
from tests.conftest import insert_reading


def test_health_still_works(client):
    rv = client.get("/health")
    assert rv.status_code == 200
    assert rv.get_json()["ok"] is True
