"""Narzędzia MCP (mcp_server/server.py) wywoływane w procesie testów przez SDK: wyniki i komunikaty błędów."""

import json

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from sqlproj_atlas.architecture.map_view import map_model
from sqlproj_atlas.core.models import DbObject, Definition
from sqlproj_atlas.mcp_server import server

from conftest import requires_engine
from mcp_client import McpClient, ToolCallError

ORDERS = "Sales|sales.orders"


def call(name: str, arguments: dict | None = None):
    """Wywołanie narzędzia jak przez Claude Code; zwraca wynik zdekodowany z JSON."""
    result = anyio.run(server.mcp.call_tool, name, arguments or {})
    return json.loads(result.content[0].text)


@pytest.fixture
def on(monkeypatch):
    """Wskazuje Workspace, na którym działają narzędzia: on(ws)."""

    def use(ws):
        monkeypatch.setattr(server, "_workspace", ws)
        monkeypatch.setenv("ATLAS_NO_BROWSER", "1")
        return ws

    return use


# ---------------------------------------------------------------------------
# Lista narzędzi i schematy
# ---------------------------------------------------------------------------


def test_all_tools_are_registered_with_descriptions():
    tools = {t.name: t for t in anyio.run(server.mcp.list_tools)}
    assert len(tools) == 17
    assert all(t.name.startswith("atlas_") and t.description for t in tools.values())


def test_tool_schemas_come_from_types():
    tools = {t.name: t for t in anyio.run(server.mcp.list_tools)}
    assign = tools["atlas_assign"].input_schema
    assert assign["required"] == ["items"]
    assert "object" in json.dumps(assign["$defs"]["AssignItem"])

    listing = tools["atlas_list_objects"].input_schema["properties"]
    assert listing["limit"]["maximum"] == 200
    assert tools["atlas_guidelines"].input_schema["properties"]["action"]["enum"] == ["get", "save"]


def test_invalid_arguments_are_reported(on, workspace):
    on(workspace)
    with pytest.raises(ToolError, match="validation error"):
        call("atlas_list_objects", {"limit": 1000})


# ---------------------------------------------------------------------------
# Przed analizą
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("atlas_list_objects", {}, "Brak analizy"),
        ("atlas_get_object", {"id": "x"}, "Brak analizy"),
        ("atlas_issues", {}, "Brak analizy"),
        ("atlas_refresh", {}, "Brak poprzedniej analizy"),
        ("atlas_changes", {}, "Brak zapisanych zmian"),
        ("atlas_save_change_notes", {"summary": "x"}, "Brak zestawu zmian"),
        ("atlas_guidelines", {"action": "save"}, "Brak treści markdown"),
    ],
)
def test_errors_before_analysis(on, workspace, tool, arguments, message):
    on(workspace)
    with pytest.raises(ToolError, match=message):
        call(tool, arguments)
    assert not workspace.data_dir.exists(), "błąd przed analizą niczego nie tworzy w projekcie"


@pytest.mark.parametrize(
    ("tool", "message"), [("atlas_refresh", "Brak poprzedniej analizy"), ("atlas_changes", "Brak zapisanych zmian")]
)
def test_errors_with_empty_database(on, workspace, monkeypatch, tool, message):
    # baza istnieje (np. po nieudanej analizie), ale nie ma w niej snapshotu ani zestawu zmian
    on(workspace)
    monkeypatch.setattr(server, "engine_status", lambda: {"ok": True})
    assert workspace.db is not None
    with pytest.raises(ToolError, match=message):
        call(tool)


def test_mcp_server_in_unrelated_folder_creates_nothing(tmp_path):
    # Claude Code startuje serwer w każdej sesji, także poza projektami SSDT
    client = McpClient(tmp_path)
    try:
        assert client.call("atlas_status")["initialized"] is False
        with pytest.raises(ToolCallError, match="Brak analizy"):
            client.call("atlas_list_objects")
    finally:
        client.close()
    assert list(tmp_path.iterdir()) == []


def test_status_configure_and_guidelines(on, workspace):
    on(workspace)
    assert call("atlas_status")["initialized"] is False
    assert call("atlas_configure", {"excludeProjects": ["Tests"]}) == {
        "saved": {"excludeProjects": ["Tests"], "excludeSchemas": []}
    }
    assert call("atlas_configure", {"excludeSchemas": ["tmp"]})["saved"] == {
        "excludeProjects": ["Tests"],
        "excludeSchemas": ["tmp"],
    }
    assert call("atlas_configure", {})["saved"]["excludeProjects"] == ["Tests"], "bez argumentu nic się nie zmienia"

    assert call("atlas_guidelines", {"action": "get"})["markdown"] is None
    call("atlas_guidelines", {"action": "save", "markdown": "## Bazy\n\nSales"})
    assert call("atlas_guidelines", {"action": "get"})["markdown"] == "## Bazy\n\nSales\n"


def test_analyze_without_engine(on, workspace, monkeypatch):
    on(workspace)
    monkeypatch.setattr(server, "engine_status", lambda: {"ok": False, "error": "Nie znaleziono .NET"})
    with pytest.raises(ToolError, match="Nie znaleziono .NET"):
        call("atlas_analyze")
    with pytest.raises(ToolError, match="Nie znaleziono .NET"):
        call("atlas_refresh")


def test_unexpected_error_details_are_hidden(on, workspace, monkeypatch):
    on(workspace)

    def crash(_ws):
        raise RuntimeError("szczegóły wewnętrzne")

    monkeypatch.setattr(server, "project_status", crash)
    with pytest.raises(ToolError) as error:
        call("atlas_status")
    assert "szczegóły" not in str(error.value)


# ---------------------------------------------------------------------------
# Po analizie
# ---------------------------------------------------------------------------


@requires_engine
def test_read_tools(on, analyzed):
    on(analyzed)
    assert call("atlas_status")["objects"] == 67
    assert call("atlas_list_objects", {"project": "DWH", "limit": 5})["total"] == 14
    assert call("atlas_get_object", {"id": "sales.Orders"})["id"] == ORDERS
    assert call("atlas_impact", {"id": "sales.Orders", "depth": 1})["object"] == ORDERS

    issues = call("atlas_issues", {"kind": "dynamic"})
    assert issues["total"] == 2
    assert call("atlas_issues", {"status": "done"})["total"] == 0
    assert call("atlas_issues", {"status": "all"})["total"] == 10


@requires_engine
def test_long_definition_is_shortened(on, analyzed):
    on(analyzed)
    with analyzed.db.session() as s:
        obj = s.query(DbObject).filter_by(id=ORDERS).first()
        s.get(Definition, obj.hash).text = "x" * 7000
    definition = call("atlas_get_object", {"id": ORDERS})["definition"]
    assert len(definition) < 6100
    assert definition.endswith("…(skrócono)")


@requires_engine
def test_aggregation_tools(on, analyzed):
    on(analyzed)
    domains = call("atlas_save_domains", {"domains": [{"project": "Sales", "name": "Zamówienia"}]})
    assert domains["domains"][0]["id"] == "Sales|zamowienia"

    assigned = call("atlas_assign", {"items": [{"object": "sales.Orders", "domain": "Sales|zamowienia"}]})
    assert assigned["assigned"] == 1
    assert assigned["objectsStillInAutoDomains"] == 66
    assert assigned["stillInAutoDomainsByProject"]["Sales"] == 44

    assert call("atlas_describe", {"items": [{"target": "project:Sales", "description": "Baza"}]})["described"] == [
        "project:Sales"
    ]

    key = call("atlas_issues", {"kind": "dynamic"})["items"][0]["key"]
    assert call("atlas_propose", {"items": [{"issue": key, "text": "Wyjaśnienie"}]})["saved"] == [key]

    with pytest.raises(ToolError, match="Nieznany projekt"):
        call("atlas_save_domains", {"domains": [{"project": "Brak", "name": "X"}]})


@requires_engine
def test_open_ui_builds_view_address(on, analyzed):
    on(analyzed)
    result = call("atlas_open_ui", {"view": "map", "object": ORDERS})
    assert result["url"].startswith("http://127.0.0.1:")
    assert result["url"].endswith("/#map/Sales%7Csales.orders")
    assert call("atlas_status")["ui"] == result["url"].split("#")[0]


@requires_engine
def test_change_tools(on, refreshed):
    on(refreshed)
    changes = call("atlas_changes")
    assert len(changes["commits"]) == 7
    assert call("atlas_changes", {"changeset": changes["changeset"]})["changeset"] == changes["changeset"]

    notes = call(
        "atlas_save_change_notes",
        {"summary": "Podsumowanie", "items": [{"object": "sales.Orders", "summary": "Zmiana"}]},
    )
    assert notes["updated"] == 1

    with pytest.raises(ToolError, match="Brak zestawu zmian"):
        call("atlas_save_change_notes", {"changeset": 999})


@requires_engine
def test_refresh_tool(on, analyzed):
    on(analyzed)
    result = call("atlas_refresh")
    assert result["changes"] == []
    assert result["openIssues"] == 10


@requires_engine
def test_analysis_without_excluded_schema(on, analyzed):
    on(analyzed)
    call("atlas_configure", {"excludeSchemas": ["Sales.audit"]})
    summary = call("atlas_analyze")

    sales = next(p for p in summary["projects"] if p["name"] == "Sales")
    assert "audit" not in sales["schemas"]
    assert summary["excludedSchemas"] == ["Sales.audit"]
    assert summary["totals"]["objects"] < 67
    with pytest.raises(ToolError, match="Nie znaleziono"):
        call("atlas_get_object", {"id": "Sales|audit.changelog"})
    assert call("atlas_get_object", {"id": "Sales|sales.orders"})["id"] == "Sales|sales.orders"

    # mapa pokazuje pominięte jako szare kafelki: schemat z liczbą obiektów i pominięty projekt
    with analyzed.db.session() as s:
        excluded = map_model(s, analyzed.files.overrides())["excluded"]
    assert excluded["projects"] == ["Tests"]
    assert excluded["schemas"]["Sales"]["audit"] >= 1


def test_open_and_close_ui(on, workspace):
    on(workspace)
    url = call("atlas_open_ui")["url"]
    assert call("atlas_close_ui") == {"closed": True, "url": url}
    assert call("atlas_close_ui") == {"closed": False, "reason": "Aplikacja nie była uruchomiona."}
