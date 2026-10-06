"""REST API aplikacji webowej (web_app/app.py): każdy endpoint, kody błędów, zabezpieczenia, pliki UI."""

import pytest
from fastapi.testclient import TestClient

from data_architect.web_app.app import create_app

from conftest import requires_engine

pytestmark = requires_engine

WRITE = {"X-Atlas": "1"}  # nagłówek wymagany przy zapisie
ORDERS = "Sales|sales.orders"


@pytest.fixture
def api(analyzed) -> TestClient:
    # Host 127.0.0.1: domyślny „testserver” odrzuciłby TrustedHostMiddleware
    with TestClient(create_app(analyzed), base_url="http://127.0.0.1") as client:
        client.ws = analyzed
        yield client


@pytest.fixture
def api_after_refresh(refreshed) -> TestClient:
    with TestClient(create_app(refreshed), base_url="http://127.0.0.1") as client:
        client.ws = refreshed
        yield client


def rev(api) -> int:
    return api.get("/api/version").json()["rev"]


# ---------------------------------------------------------------------------
# Zabezpieczenia i pliki UI
# ---------------------------------------------------------------------------


def test_foreign_host_is_rejected(api):
    assert api.get("/api/ping", headers={"Host": "evil.example"}).status_code == 400
    assert api.get("/api/ping", headers={"Host": "localhost:1234"}).status_code == 200


@pytest.mark.parametrize("path", ["/api/object", "/api/domain", "/api/domain/create", "/api/note", "/api/issue",
                                  "/api/guidelines"])  # fmt: skip
def test_write_requires_header(api, path):
    response = api.post(path, json={})
    assert response.status_code == 403
    assert response.json() == {"detail": "missing header"}


def test_static_files(api):
    index = api.get("/")
    assert index.status_code == 200
    assert "Data Architect" in index.text
    assert api.get("/app.js").headers["content-type"].startswith("text/javascript")
    assert api.get("/app.css").headers["content-type"].startswith("text/css")
    assert api.get("/nie-ma.js").status_code == 404
    assert api.get("/%2e%2e/%2e%2e/pyproject.toml").status_code == 404


def test_responses_are_not_cached(api):
    assert api.get("/api/ping").headers["cache-control"] == "no-store"
    assert api.get("/").headers["cache-control"] == "no-store"


# ---------------------------------------------------------------------------
# Odczyt
# ---------------------------------------------------------------------------


def test_ping_and_state(api):
    assert api.get("/api/ping").json() == {"ok": True, "project": str(api.ws.project_dir)}

    state = api.get("/api/state").json()
    assert state["initialized"] is True
    assert (state["objects"], state["newCommits"], state["changeset"]) == (67, 0, None)
    assert state["project"] == "sklep"
    assert state["engine"] is True


def test_state_after_refresh(api_after_refresh):
    state = api_after_refresh.get("/api/state").json()
    assert state["changeset"]["changed"] == 10


def test_state_before_analysis(workspace):
    with TestClient(create_app(workspace), base_url="http://127.0.0.1") as client:
        state = client.get("/api/state").json()
        assert (state["initialized"], state["snapshot"], state["objects"]) == (False, None, 0)
        assert client.get("/api/model").json() == {"initialized": False}
        assert client.get("/api/issues").json() == []
        assert client.get("/api/changes").json() is None


def test_model_object_and_issues(api):
    assert len(api.get("/api/model").json()["objects"]) == 67
    assert api.get("/api/object", params={"id": ORDERS}).json()["name"] == "Orders"
    assert api.get("/api/object", params={"id": "Sales|nie.ma"}).json() == {"error": "not found"}
    assert api.get("/api/object").status_code == 422, "brak parametru id"
    assert len(api.get("/api/issues").json()) == 10


def test_changes(api_after_refresh):
    latest = api_after_refresh.get("/api/changes").json()
    assert latest["stats"]["commits"] == 7
    assert api_after_refresh.get("/api/changes", params={"id": latest["id"]}).json()["id"] == latest["id"]
    assert api_after_refresh.get("/api/changes", params={"id": 999}).json() is None


# ---------------------------------------------------------------------------
# Poprawki użytkownika
# ---------------------------------------------------------------------------


def test_edit_object_sets_and_clears_fields(api):
    before = rev(api)
    assert api.post(
        "/api/object", json={"id": ORDERS, "description": "Opis", "domain": "Sales|x"}, headers=WRITE
    ).json() == {"ok": True}
    assert rev(api) == before + 1

    # pole pominięte w żądaniu zostaje bez zmian, pusta wartość usuwa poprawkę
    api.post("/api/object", json={"id": ORDERS, "domain": ""}, headers=WRITE)
    entry = api.ws.files.overrides().objects[ORDERS]
    assert (entry.description, entry.domain) == ("Opis", None)


def test_edit_object_requires_id(api):
    assert api.post("/api/object", json={"description": "x"}, headers=WRITE).status_code == 422


def test_edit_domain_and_note(api):
    api.post("/api/domain", json={"id": "auto|Sales|crm", "name": "Klienci"}, headers=WRITE)
    api.post("/api/note", json={"target": "ext|crm", "description": "Salesforce", "name": "SF"}, headers=WRITE)

    ov = api.ws.files.overrides()
    assert ov.domains["auto|Sales|crm"].name == "Klienci"
    assert (ov.notes["ext|crm"].description, ov.notes["ext|crm"].name) == ("Salesforce", "SF")
    domain = next(d for d in api.get("/api/model").json()["domains"] if d["id"] == "auto|Sales|crm")
    assert (domain["name"], domain["nameOrigin"]) == ("Klienci", "user")


def test_create_domain(api):
    created = api.post("/api/domain/create", json={"project": "Sales", "name": "Nowa"}, headers=WRITE).json()
    assert created["id"].startswith("Sales|user-")
    assert api.post("/api/domain/create", json={"project": "Sales"}, headers=WRITE).status_code == 422


def test_guidelines(api):
    assert api.get("/api/guidelines").json()["markdown"] == ""
    api.post("/api/guidelines", json={"markdown": "## A\n\nTekst"}, headers=WRITE)
    assert api.get("/api/guidelines").json()["markdown"] == "## A\n\nTekst\n"


# ---------------------------------------------------------------------------
# Pozycje do wyjaśnienia
# ---------------------------------------------------------------------------


def test_issue_decisions(api):
    issues = api.get("/api/issues").json()
    dynamic = next(i for i in issues if i["kind"] == "dynamic")

    assert api.post("/api/issue", json={"key": dynamic["key"], "action": "skip"}, headers=WRITE).status_code == 200
    assert next(i for i in api.get("/api/issues").json() if i["key"] == dynamic["key"])["status"] == "skip"

    api.post("/api/issue", json={"key": dynamic["key"], "action": "reopen"}, headers=WRITE)
    api.post("/api/issue", json={"key": dynamic["key"], "action": "resolve", "target": ORDERS, "targetIsObject": True,
                                 "kind": "writes", "text": "Pisze do zamówień"}, headers=WRITE)  # fmt: skip
    edges = api.get("/api/model").json()["edges"]
    assert any(e["dst"] == ORDERS and e["origin"] == "user" and e["kind"] == "writes" for e in edges)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"key": "brak", "action": "skip"}, "Nieznana pozycja"),
        ({"action": "skip"}, None),
    ],
)
def test_issue_errors(api, body, message):
    response = api.post("/api/issue", json=body, headers=WRITE)
    if message:
        assert (response.status_code, response.json()) == (400, {"error": message})
    else:
        assert response.status_code == 422


def test_issue_unknown_action_and_accept_without_proposal(api):
    key = api.get("/api/issues").json()[0]["key"]
    assert api.post("/api/issue", json={"key": key, "action": "zrob"}, headers=WRITE).json() == {
        "error": "Nieznana akcja: zrob"
    }
    assert api.post("/api/issue", json={"key": key, "action": "accept"}, headers=WRITE).json() == {
        "error": "Brak propozycji"
    }


# ---------------------------------------------------------------------------
# UI nie uruchamia operacji (analizę i odświeżenie wywołuje tylko Claude przez MCP)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/refresh", "/api/analyze"])
def test_ui_cannot_run_analysis(api, path):
    assert api.post(path, json={}, headers=WRITE).status_code in (404, 405)


def test_state_with_missing_analysis_commit(api):
    from data_architect.core.models import Snapshot

    with api.ws.db.session() as s:
        s.query(Snapshot).update({Snapshot.commit_sha: "0" * 40})
    assert api.get("/api/state").json()["newCommits"] == 0
