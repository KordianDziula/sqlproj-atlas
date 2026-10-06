"""Test end-to-end: serwer MCP po stdio (oficjalny klient SDK) + silnik DacFx + REST API na projekcie testowym.

Kroki zależą od siebie (wspólny projekt i serwer w fixturze `env`), więc wykonują się w kolejności z pliku:
analiza → agregacja przez „Claude'a” → poprawki użytkownika w API → nowe commity → odświeżenie → zmiany lokalne.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from conftest import requires_engine
from make_test_project import make
from mcp_client import McpClient, ToolCallError

pytestmark = requires_engine

UI_HEADERS = {"X-Atlas": "1"}  # nagłówek wymagany przy zapisie przez API


@dataclass
class Env:
    dir: Path
    mcp: McpClient
    ui: httpx.Client | None = None

    def get(self, path: str):
        return self.ui.get("api/" + path).json()

    def post(self, path: str, body: dict) -> httpx.Response:
        return self.ui.post("api/" + path, json=body, headers=UI_HEADERS)

    def detail(self, object_id: str) -> dict:
        return self.get("object?id=" + quote(object_id, safe=""))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    project = make(tmp_path_factory.mktemp("e2e") / "sklep", "A")
    client = McpClient(project)
    environment = Env(dir=project, mcp=client)
    yield environment
    if environment.ui:
        environment.ui.close()
    client.close()


# ---------------------------------------------------------------------------
# Analiza
# ---------------------------------------------------------------------------


def test_tools_list(env):
    names = env.mcp.list_tools()
    for name in ("atlas_status", "atlas_analyze", "atlas_refresh", "atlas_changes", "atlas_assign", "atlas_open_ui"):
        assert name in names

    with pytest.raises(ToolCallError, match="Unknown tool"):
        env.mcp.call("atlas_nieznane")


def test_status_before_analysis(env):
    status = env.mcp.call("atlas_status")
    assert status["initialized"] is False
    assert status["engine"]["ok"] is True
    assert len(status["sqlProjectsFound"]) == 4

    with pytest.raises(ToolCallError, match="Brak poprzedniej analizy"):
        env.mcp.call("atlas_refresh")


def test_analyze(env):
    env.mcp.call("atlas_configure", {"excludeProjects": ["Tests"]})
    summary = env.mcp.call("atlas_analyze")
    by_name = {p["name"]: p for p in summary["projects"]}

    assert sorted(by_name) == ["DWH", "Sales", "Staging"]
    assert (by_name["Sales"]["objects"], by_name["Staging"]["objects"], by_name["DWH"]["objects"]) == (45, 8, 14)
    assert by_name["Sales"]["format"] == "SDK-style"
    assert summary["crossDatabaseEdges"] == 4
    assert sorted(x["name"] for x in summary["externalSystems"]) == ["Archive", "CRM", "CRM_LINK", "ErpDb", "ImportDb"]
    assert summary["issues"]["dynamic"]["count"] == 2
    assert summary["issues"]["unresolved"]["count"] == 1
    assert summary["issues"]["external"]["count"] == 7
    assert summary["namingPrefixes"]["usp_"]["count"] >= 10


def test_object_detail(env):
    orders = env.mcp.call("atlas_get_object", {"id": "sales.Orders"})
    assert orders["id"] == "Sales|sales.orders"
    assert [c["name"] for c in orders["columns"] if c.get("pk")] == ["OrderId"]
    assert any(c["name"] == "DiscountCode" for c in orders["columns"])

    kinds = {u["name"]: u["kind"] for u in orders["usedBy"]}
    assert kinds["sales.usp_CreateOrder"] == "writes"
    assert kinds["sales.usp_GetOrderSummary"] == "reads"
    assert kinds["sales.trg_Orders_Audit"] == "on"
    assert kinds["sales.OrderItems"] == "fk"
    assert any(u.get("project") == "Staging" for u in orders["usedBy"]), "odwołanie z innej bazy przez $(Sales)"

    procedure = env.mcp.call("atlas_get_object", {"id": "usp_CreateOrder"})
    assert [p["name"] for p in procedure["params"]] == ["@CustomerId", "@Items", "@OrderId"]
    assert procedure["params"][2]["output"] is True

    with pytest.raises(ToolCallError, match="Nie znaleziono"):
        env.mcp.call("atlas_get_object", {"id": "nie.istnieje"})


# ---------------------------------------------------------------------------
# Agregacja architektury (to, co robi Claude w /ssdt-atlas:init)
# ---------------------------------------------------------------------------


def test_aggregation(env):
    domains = env.mcp.call(
        "atlas_save_domains",
        {
            "domains": [
                {"project": "Sales", "name": "Zamówienia", "description": "Cykl życia zamówienia."},
                {"project": "Sales", "name": "Klienci", "description": "Dane klientów."},
            ]
        },
    )
    assert [d["id"] for d in domains["domains"]] == ["Sales|zamowienia", "Sales|klienci"]

    sales = env.mcp.call("atlas_list_objects", {"project": "Sales", "schema": "sales", "limit": 100})
    assert sales["total"] == 12

    result = env.mcp.call(
        "atlas_assign",
        {
            "items": [{"object": i["id"], "domain": "Sales|zamowienia"} for i in sales["items"]]
            + [
                {"object": "Sales|sales.orders", "description": "Nagłówek zamówienia."},
                {"object": "Sales|crm.customers", "domain": "Sales|zamowienia"},
                {"object": "nie.istnieje", "domain": "Sales|zamowienia"},
                {"object": "Staging|stg.stg_orders", "domain": "Sales|zamowienia"},  # domena z innej bazy
            ]
        },
    )
    assert (result["assigned"], result["described"], len(result["errors"])) == (13, 1, 2)
    assert env.mcp.call("atlas_list_objects", {"unassigned": True, "limit": 1})["total"] == 67 - 13

    described = env.mcp.call(
        "atlas_describe",
        {
            "items": [
                {"target": "project:Sales", "description": "Baza transakcyjna."},
                {"target": "ext|crm", "description": "Salesforce"},
                {"target": "domain:Sales|klienci", "description": "Klienci i zgody."},
                {"target": "zly", "description": "x"},
            ]
        },
    )
    assert (len(described["described"]), len(described["errors"])) == (3, 1)

    purge = next(
        i for i in env.mcp.call("atlas_issues", {"kind": "dynamic"})["items"] if i["object"] == "dbo.usp_PurgeTable"
    )
    proposal = {
        "issue": purge["key"],
        "text": "Czyści tabele z cfg.PurgeTables",
        "targets": [{"id": "cfg.PurgeTables", "kind": "writes"}],
        "confidence": 40,
    }
    assert env.mcp.call("atlas_propose", {"items": [proposal]})["saved"] == [purge["key"]]

    saved = env.mcp.call(
        "atlas_guidelines", {"action": "save", "markdown": "# Wskazówki\n\n## Bazy\n\nSales, Staging, DWH.\n"}
    )
    assert Path(saved["saved"]).exists()

    impact = env.mcp.call("atlas_impact", {"id": "sales.Orders"})
    assert impact["dependents"] >= 10
    assert any(i["id"] == "Sales|rpt.usp_monthlykpi" and i["depth"] == 2 for i in impact["items"]), (
        "zależność przez widok"
    )


# ---------------------------------------------------------------------------
# Aplikacja webowa i poprawki użytkownika
# ---------------------------------------------------------------------------


def test_web_app_and_security(env):
    url = env.mcp.call("atlas_open_ui", {"view": "map"})["url"]
    env.ui = httpx.Client(base_url=re.sub("#.*$", "", url), timeout=600)

    assert "SSDT Atlas" in env.ui.get("").text
    assert env.ui.get("app.js").status_code == 200
    assert env.ui.get("../../etc/passwd").status_code == 404

    # zapis bez nagłówka X-Atlas i żądanie z obcym Host są odrzucane
    assert env.ui.post("api/object", json={"id": "x"}).status_code == 403
    assert env.ui.get("api/state", headers={"Host": "evil.example"}).status_code == 400


def test_map_model(env):
    model = env.get("model")
    assert len(model["objects"]) == 67
    assert [p["name"] for p in model["projects"]] == ["Sales", "Staging", "DWH"], "kolejność według przepływu danych"
    assert next(d for d in model["domains"] if d["id"] == "Sales|zamowienia")["count"] == 13
    assert any(e["origin"] == "claude" and e["unsure"] for e in model["edges"]), (
        "propozycja Claude'a jako niepewna relacja"
    )


def test_user_overrides_win(env):
    assert (
        env.post(
            "object", {"id": "Sales|sales.orders", "description": "Opis od użytkownika", "domain": "Sales|klienci"}
        ).status_code
        == 200
    )

    overrides = json.loads((env.dir / ".claude" / "ssdt-atlas" / "overrides.json").read_text(encoding="utf-8"))
    assert overrides["objects"]["Sales|sales.orders"]["description"] == "Opis od użytkownika"

    orders = env.detail("Sales|sales.orders")
    assert (orders["description"], orders["descOrigin"], orders["domain"]) == (
        "Opis od użytkownika",
        "user",
        "Sales|klienci",
    )

    # Claude nie nadpisuje poprawek użytkownika i dostaje o nich informację
    result = env.mcp.call(
        "atlas_assign",
        {"items": [{"object": "Sales|sales.orders", "domain": "Sales|zamowienia", "description": "Nowy opis Claude"}]},
    )
    assert len(result["userOverridesKept"]) == 2
    assert env.detail("Sales|sales.orders")["description"] == "Opis od użytkownika"


def test_issue_decisions(env):
    issues = env.get("issues")

    # zatwierdzenie propozycji zamienia niepewną relację w relację użytkownika
    purge = next(i for i in issues if i["object"] == "dbo.usp_PurgeTable")
    assert env.post("issue", {"key": purge["key"], "action": "accept"}).status_code == 200
    edges = env.get("model")["edges"]
    assert any(
        e["src"] == "Sales|dbo.usp_purgetable" and e["dst"] == "Sales|cfg.purgetables" and e["origin"] == "user"
        for e in edges
    )

    # opis systemu zewnętrznego zamyka wszystkie jego pozycje
    crm = next(i for i in issues if i["extId"] == "ext|crm")
    env.post("issue", {"key": crm["key"], "action": "resolve", "text": "Salesforce", "ext": True})
    assert all(i["status"] == "done" for i in env.get("issues") if i["extId"] == "ext|crm")

    # pominięcie i cofnięcie
    legacy = next(i for i in issues if i["object"] == "rpt.vw_LegacyKpi")
    env.post("issue", {"key": legacy["key"], "action": "skip"})
    assert next(i for i in env.get("issues") if i["key"] == legacy["key"])["status"] == "skip"
    env.post("issue", {"key": legacy["key"], "action": "reopen"})
    assert next(i for i in env.get("issues") if i["key"] == legacy["key"])["status"] == "open"


def test_guidelines_and_user_domain(env):
    env.post("guidelines", {"markdown": "# W\n\n## A\n\nTekst\n"})
    assert "## A" in env.get("guidelines")["markdown"]

    created = env.post("domain/create", {"project": "Sales", "name": "Moja domena"}).json()
    assert re.match(r"^Sales\|user-", created["id"])


# ---------------------------------------------------------------------------
# Odświeżenie po nowych commitach
# ---------------------------------------------------------------------------


def test_refresh_after_commits(env):
    make(env.dir, "B")
    assert env.mcp.call("atlas_status")["newCommitsSinceAnalysis"] == 7

    result = env.mcp.call("atlas_refresh")
    by_id = {c["object"]: c for c in result["changes"]}

    assert by_id["Sales|inv.usp_recalcstock_old"]["type"] == "removed"
    assert by_id["Sales|sales.usp_searchorders"]["type"] == "added"
    assert by_id["Sales|pay.usp_refund"]["type"] == "added"
    assert by_id["Sales|pay.paymenttransactions"]["type"] == "renamed"
    assert by_id["Sales|pay.paymenttransactions"]["oldName"] == "pay.Transactions"

    orders = by_id["Sales|sales.orders"]
    assert orders["type"] == "modified"
    assert orders["columnsRemoved"] == ["DiscountCode"]
    assert orders["risk"] == "high"
    assert sorted(b["object"] for b in orders["brokenBy"]) == [
        "Sales|rpt.vw_salesreport",
        "Sales|sales.usp_getordersummary",
    ]

    assert by_id["Sales|sales.usp_createorder"]["paramsAdded"] == ["@CurrencyCode"]
    assert by_id["Sales|inv.stocklevels"]["type"] == "modified", "zmiana indeksu zmienia tabelę"
    assert by_id["Sales|sales.usp_searchorders"]["risk"] == "med", "dynamiczny SQL"
    assert "Sales|pay.usp_refund|calls" in by_id["Sales|sales.usp_cancelorder"]["depsAdded"]
    assert result["stats"]["commits"] == 7
    assert "Sales|sales.usp_searchorders" in result["newObjectsInAutoDomains"]


def test_change_notes_and_commit_list(env):
    changes = env.mcp.call("atlas_changes")
    notes = env.mcp.call(
        "atlas_save_change_notes",
        {
            "summary": "Podsumowanie.",
            "items": [
                {"object": "sales.Orders", "summary": "Usunięto DiscountCode.", "risk": "high"},
                {"object": "brak", "summary": "x"},
            ],
        },
    )
    assert (notes["updated"], len(notes["errors"])) == (1, 1)

    view = env.get("changes")
    assert view["summary"] == "Podsumowanie."
    assert view["changes"][0]["risk"] == "high"

    # zmiana „było → jest” z różnicą definicji liczoną z dwóch analiz
    orders_change = next(c for c in view["changes"] if c["objectId"] == "Sales|sales.orders")
    assert any(kind == "d" and "DiscountCode" in text for kind, text in orders_change["diff"])
    renamed = next(c for c in view["changes"] if c["type"] == "renamed")
    assert renamed["oldName"] == "pay.Transactions"

    # commity od poprzedniej analizy: sam kontekst (skrót, autor, data, opis), od najnowszego, bez wiązania z obiektami
    commits = view["stats"]["commitList"]
    assert len(commits) == 7
    assert commits[0]["message"] == "Index for stock lookups"
    assert set(commits[0]) == {"sha", "author", "date", "message"}
    assert any(c.endswith("Remove legacy discounts") for c in changes["commits"])

    # poprawki użytkownika przetrwały odświeżenie; notatka o zmianie jest w szczegółach obiektu
    assert env.detail("Sales|sales.orders")["description"] == "Opis od użytkownika"
    orders = env.mcp.call("atlas_get_object", {"id": "sales.Orders"})
    assert any(c["summary"] == "Usunięto DiscountCode." for c in orders["changeNotes"])


def test_refresh_without_changes(env):
    assert env.mcp.call("atlas_refresh")["changes"] == []
    assert env.mcp.call("atlas_changes")["changes"] == []


def test_ui_cannot_run_analysis(env):
    assert env.post("refresh", {}).status_code in (404, 405)


def test_local_changes(env):
    categories = env.dir / "src" / "Sales" / "cat" / "Tables" / "Categories.sql"
    with categories.open("a", encoding="utf-8", newline="") as f:
        f.write("\r\nGO\r\nCREATE NONCLUSTERED INDEX [IX_Categories_Name] ON [cat].[Categories]([Name]);")
    (env.dir / "src" / "Sales" / "cat" / "Views" / "vw_Brand.sql").write_text(
        "CREATE VIEW [cat].[vw_Brand] AS SELECT [Name] FROM [cat].[Categories];", encoding="utf-8"
    )

    result = env.mcp.call("atlas_refresh")
    assert result["to"]["dirty"] is True
    assert sorted(f"{c['object']}:{c['type']}" for c in result["changes"]) == [
        "Sales|cat.categories:modified",
        "Sales|cat.vw_brand:added",
    ]

    # niezatwierdzone zmiany to po prostu nowy stan: bez commitów, z różnicą definicji
    view = env.get("changes")
    assert view["stats"]["commitList"] == []
    new_view = next(c for c in view["changes"] if c["objectId"] == "Sales|cat.vw_brand")
    assert all(kind == "a" for kind, _ in new_view["diff"]), "nowy obiekt: cała definicja jako dodana"
    index = next(c for c in view["changes"] if c["objectId"] == "Sales|cat.categories")
    assert any(kind == "a" and "IX_Categories_Name" in text for kind, text in index["diff"])
