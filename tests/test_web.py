import json
import math

import pytest

from forge.staff.office import Office
from forge.web.server import OfficeService, create_app, json_safe


@pytest.fixture
def client(make_cfg):
    cfg = make_cfg()
    office = Office(cfg)
    service = OfficeService(cfg, office=office, autostart=False)
    app = create_app(cfg, service)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        c.service = service
        yield c
    service.close()


def test_the_page_loads(client):
    assert client.get("/").status_code == 200


def test_state_is_valid_json_for_a_browser(client):
    """Regression: an infinite payback ratio made the whole page blank."""
    client.service.standup()
    raw = client.get("/api/state").get_data(as_text=True)
    assert "Infinity" not in raw and "NaN" not in raw
    data = json.loads(raw)
    assert data["treasury"]["seed_cents"] == 5000
    assert {v["name"] for v in data["ventures"]} == {"coloringbook", "memecoin"}
    assert len(data["staff"]) == 5


def test_json_safe_strips_non_finite_values():
    cleaned = json_safe({"a": math.inf, "b": [math.nan, 1.5], "c": {"d": -math.inf}, "e": "fine"})
    assert cleaned == {"a": None, "b": [None, 1.5], "c": {"d": None}, "e": "fine"}
    json.dumps(cleaned)  # must not raise


def test_chat_round_trip(client):
    posted = client.post("/api/chat/manager", json={"message": "how are we doing?"})
    assert posted.status_code == 200
    assert posted.get_json()["answer"].strip()

    thread = client.get("/api/chat/manager").get_json()["messages"]
    assert [m["speaker"] for m in thread[-2:]] == ["user", "staff"]


def test_chat_validates_its_input(client):
    assert client.post("/api/chat/janitor", json={"message": "hi"}).status_code == 404
    assert client.post("/api/chat/manager", json={"message": "   "}).status_code == 400
    assert client.get("/api/chat/janitor").status_code == 404


def test_pausing_and_starting_the_office(client):
    assert client.post("/api/running", json={"running": True}).get_json()["running"] is True
    assert client.post("/api/running", json={"running": False}).get_json()["running"] is False


def test_tasks_can_be_resolved(client):
    client.service.standup()
    tasks = client.get("/api/state").get_json()["tasks"]
    assert tasks, "the book venture should have queued an upload"
    assert client.post(f"/api/tasks/{tasks[0]['id']}", json={"status": "done"}).status_code == 200
    assert client.post("/api/tasks/99999", json={"status": "done"}).status_code == 404


def test_booking_income_is_validated(client):
    assert client.post("/api/income", json={"venture": "coloringbook", "amount": 4.5}).status_code == 200
    assert client.post("/api/income", json={"venture": "coloringbook", "amount": -1}).status_code == 400
    assert client.post("/api/income", json={"venture": "nope", "amount": 1}).status_code == 400
    assert client.post("/api/income", json={"venture": "coloringbook", "amount": "x"}).status_code == 400
    books = client.service.office.ledger.books("coloringbook")
    assert books.income == 450


def test_standup_endpoint_runs_in_the_background(client):
    assert client.post("/api/standup").get_json()["started"] is True


def test_only_one_standup_runs_at_a_time(client):
    import threading

    gate = threading.Event()
    original = client.service.office.standup

    def slow(*args, **kwargs):
        gate.wait(5)
        return original(*args, **kwargs)

    client.service.office.standup = slow
    try:
        assert client.post("/api/standup").get_json()["started"] is True
        second = client.post("/api/standup").get_json()
        assert second["started"] is False
        assert "already running" in second["reason"]
    finally:
        gate.set()
        client.service.close()


def test_closing_waits_for_a_standup_in_flight(client):
    """Regression: closing the ledger under a running standup segfaulted."""
    client.post("/api/standup")
    client.service.close()
    assert client.service.standup() == {"error": "the office is closed"}


def test_venture_actions(client):
    assert client.post("/api/venture/memecoin/revive").status_code == 200
    assert client.post("/api/venture/memecoin/unbench").status_code == 200
    assert client.post("/api/venture/memecoin/sabotage").status_code == 400


def test_a_failing_standup_is_reported_not_swallowed(client, monkeypatch):
    monkeypatch.setattr(client.service.office, "standup", _boom)
    result = client.service.standup()
    assert "error" in result
    assert client.get("/api/state").get_json()["last_error"]


def _boom(*args, **kwargs):
    raise RuntimeError("the office caught fire")
