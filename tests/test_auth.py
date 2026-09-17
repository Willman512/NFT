"""The door. Off localhost, a token is required — that is not configurable."""

import json

import pytest

from forge.staff.office import Office
from forge.web import auth
from forge.web.server import OfficeService, create_app


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("localhost", True), ("::1", True), ("", True),
    ("0.0.0.0", False), ("192.168.1.20", False), ("10.0.0.4", False),
    ("example.com", False), ("not an address", False),
])
def test_what_counts_as_only_this_machine(host, expected):
    assert auth.is_loopback(host) is expected


def test_token_comparison_rejects_the_obvious_mistakes():
    token = auth.new_token()
    assert auth.matches(token, token)
    assert not auth.matches(None, token)
    assert not auth.matches("", token)
    assert not auth.matches(token[:-1], token)
    assert not auth.matches(token + "x", token)


def test_link_carries_the_token_only_when_there_is_one():
    assert auth.link("127.0.0.1", 8765, None) == "http://localhost:8765/"
    assert auth.link("192.168.1.9", 8765, "abc") == "http://192.168.1.9:8765/?t=abc"


def test_a_token_is_only_minted_once_the_office_is_exposed(make_cfg, tmp_path):
    from forge.core.ledger import Ledger

    cfg = make_cfg()
    with Ledger(tmp_path / "a.db") as ledger:
        assert auth.resolve_token(cfg, ledger) is None
        cfg.office.host = "0.0.0.0"
        first = auth.resolve_token(cfg, ledger)
        assert first
        # Stable across restarts, so the link you saved keeps working.
        assert auth.resolve_token(cfg, ledger) == first


def test_a_configured_token_wins(make_cfg, tmp_path):
    from forge.core.ledger import Ledger

    cfg = make_cfg()
    cfg.office.host = "0.0.0.0"
    cfg.office.token = "  chosen-by-hand  "
    with Ledger(tmp_path / "b.db") as ledger:
        assert auth.resolve_token(cfg, ledger) == "chosen-by-hand"


# ----------------------------------------------------------------------
@pytest.fixture
def locked(make_cfg):
    """An office bound somewhere other than loopback, so the door is on."""
    cfg = make_cfg()
    cfg.office.host = "0.0.0.0"
    office = Office(cfg)
    service = OfficeService(cfg, office=office, autostart=False)
    app = create_app(cfg, service)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        c.token = app.config["FORGE_TOKEN"]
        yield c
    service.close()


def test_the_door_is_on_when_exposed(locked):
    assert locked.token, "an exposed office must have a token"
    assert locked.get("/").status_code == 401
    assert locked.get("/api/state").status_code == 401


def test_an_api_refusal_is_json_a_page_refusal_is_html(locked):
    api = locked.get("/api/state")
    assert api.get_json()["error"] == "not signed in"
    page = locked.get("/")
    assert "locked" in page.get_data(as_text=True)
    assert "?t=" in page.get_data(as_text=True)


def test_the_link_signs_you_in_and_then_gets_out_of_the_url(locked):
    response = locked.get(f"/?t={locked.token}")
    assert response.status_code == 302
    assert response.headers["Location"] == "/"

    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    # The client keeps the cookie, so the next request is allowed.
    assert locked.get("/").status_code == 200


def test_other_query_parameters_survive_the_redirect(locked):
    response = locked.get(f"/?t={locked.token}&tab=ideas")
    assert response.headers["Location"] == "/?tab=ideas"


def test_a_header_works_too_for_anything_scripted(locked):
    response = locked.get("/api/state", headers={auth.HEADER: locked.token})
    assert response.status_code == 200


def test_a_wrong_token_gets_nowhere(locked):
    assert locked.get("/?t=nope").status_code == 401
    assert locked.get("/api/state", headers={auth.HEADER: "nope"}).status_code == 401
    locked.set_cookie(auth.COOKIE, "nope", domain="localhost")
    assert locked.get("/api/state").status_code == 401


def test_writes_are_refused_without_a_token(locked):
    assert locked.post("/api/ideas", json={"body": "let me in"}).status_code == 401
    assert locked.post("/api/standup").status_code == 401
    assert locked.post("/api/income", json={"venture": "x", "amount": 1}).status_code == 401


def test_the_shell_a_phone_needs_before_signing_in_stays_open(locked):
    assert locked.get("/healthz").status_code == 200
    assert locked.get("/manifest.webmanifest").status_code == 200


def test_a_loopback_office_asks_for_nothing(client):
    assert client.get("/").status_code == 200
    assert client.get("/api/state").status_code == 200


# ----------------------------------------------------------------------
def test_the_manifest_describes_an_installable_app(client):
    manifest = client.get("/manifest.webmanifest").get_json()
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    sizes = {icon.get("sizes") for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    json.dumps(manifest)


def test_the_service_worker_is_served_from_the_root(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["Service-Worker-Allowed"] == "/"
    body = response.get_data(as_text=True)
    assert "/api/" in body, "the worker must know not to cache live state"
