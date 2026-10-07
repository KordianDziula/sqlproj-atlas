"""Widoki dla UI i Claude'a: mapa, szczegóły obiektu, katalog, pozycje do wyjaśnienia, zmiany, podsumowanie, stan."""

import pytest

from sqlproj_atlas.architecture.catalog import list_objects
from sqlproj_atlas.architecture.editing import DomainInput, ProposalInput, ProposalTarget, propose, save_domains
from sqlproj_atlas.architecture.issues_view import issues_view
from sqlproj_atlas.architecture.map_view import _order_by_data_flow, map_model
from sqlproj_atlas.architecture.object_view import impact, object_detail
from sqlproj_atlas.change_analysis.changes_view import changeset_for_claude, changeset_view, find_changeset
from sqlproj_atlas.core.models import Snapshot
from sqlproj_atlas.mcp_server.status import project_status
from sqlproj_atlas.project_analysis.summary import analysis_summary

from conftest import requires_engine

ORDERS = "Sales|sales.orders"


def edge(src_db: str, dst_db: str) -> dict:
    return {"src": f"{src_db}|a.b", "dst": f"{dst_db}|c.d"}


# ---------------------------------------------------------------------------
# Kolejność baz (bez silnika)
# ---------------------------------------------------------------------------


def test_databases_ordered_by_data_flow():
    # DWH czyta ze Staging, Staging z Sales → źródła przed odbiorcami
    assert _order_by_data_flow(["DWH", "Sales", "Staging"], [edge("DWH", "Staging"), edge("Staging", "Sales")]) == [
        "Sales",
        "Staging",
        "DWH",
    ]


def test_databases_without_relations_are_alphabetical():
    assert _order_by_data_flow(["Zeta", "Alfa"], []) == ["Alfa", "Zeta"]


def test_cycle_between_databases_does_not_hang():
    assert sorted(_order_by_data_flow(["A", "B"], [edge("A", "B"), edge("B", "A")])) == ["A", "B"]


def test_external_systems_do_not_affect_order():
    assert _order_by_data_flow(["B", "A"], [{"src": "A|x.y", "dst": "ext|crm"}]) == ["A", "B"]


# ---------------------------------------------------------------------------
# Mapa
# ---------------------------------------------------------------------------


@requires_engine
def test_map_model_structure(analyzed):
    with analyzed.db.session() as s:
        model = map_model(s, analyzed.files.overrides())

    assert model["initialized"] is True
    assert [p["name"] for p in model["projects"]] == ["Sales", "Staging", "DWH"]
    assert len(model["objects"]) == 67
    assert model["changeset"] is None
    sales = next(p for p in model["projects"] if p["name"] == "Sales")
    assert (sales["count"], sales["format"]) == (45, "sdk")

    # każdy obiekt ma domenę z listy domen; kolory domen są cykliczne w obrębie bazy (1–8)
    domain_ids = {d["id"] for d in model["domains"]}
    assert all(o["domain"] in domain_ids for o in model["objects"])
    assert all(1 <= d["color"] <= 8 for d in model["domains"])
    assert sum(d["count"] for d in model["domains"]) == 67


@requires_engine
def test_map_model_includes_user_edges_and_open_proposals(analyzed):
    analyzed.files.set_override("objects", ORDERS, "description", "x")  # dowolna poprawka nie psuje modelu
    with analyzed.files.edit_overrides() as ov:
        ov.add_edge(ORDERS, "ext|crm", "reads", "test")
        ov.add_edge("Sales|nie.istnieje", ORDERS, "reads", "ignorowana: brak obiektu")

    with analyzed.db.session() as s:
        key = next(i["key"] for i in issues_view(s, analyzed.files.overrides()) if i["object"] == "dbo.usp_PurgeTable")
        propose(s, [ProposalInput(issue=key, text="x", targets=[ProposalTarget(id="cfg.PurgeTables", kind="writes")])])

    with analyzed.db.session() as s:
        edges = map_model(s, analyzed.files.overrides())["edges"]
    assert {"src": ORDERS, "dst": "ext|crm", "kind": "reads", "origin": "user", "unsure": False} in edges
    assert not any(e["src"] == "Sales|nie.istnieje" for e in edges)
    assert any(e["origin"] == "claude" and e["unsure"] and e["dst"] == "Sales|cfg.purgetables" for e in edges)

    # po pominięciu pozycji propozycja znika z mapy
    analyzed.files.set_override("issues", key, "status", "skip")
    with analyzed.db.session() as s:
        assert not any(e["origin"] == "claude" for e in map_model(s, analyzed.files.overrides())["edges"])


@requires_engine
def test_map_model_marks_changed_objects(refreshed):
    with refreshed.db.session() as s:
        model = map_model(s, refreshed.files.overrides())
    by_id = {o["id"]: o for o in model["objects"]}
    assert by_id["Sales|sales.usp_searchorders"]["changed"] == "added"
    assert by_id[ORDERS]["changed"] == "modified"
    assert model["changeset"]["count"] == 10


def test_map_model_without_analysis(db):
    from sqlproj_atlas.core.project_files import Overrides

    with db.session() as s:
        assert map_model(s, Overrides()) == {"initialized": False}


# ---------------------------------------------------------------------------
# Szczegóły obiektu i wpływ
# ---------------------------------------------------------------------------


@requires_engine
def test_object_detail(analyzed):
    with analyzed.db.session() as s:
        detail = object_detail(s, analyzed.files.overrides(), ORDERS)

    assert (detail["name"], detail["type"], detail["t"]) == ("Orders", "Table", "T")
    assert "CREATE TABLE [sales].[Orders]" in detail["definition"]
    assert detail["domain"] == "auto|Sales|sales"
    assert any(u["name"] == "sales.usp_CreateOrder" and u["kind"] == "writes" for u in detail["usedBy"])


@requires_engine
def test_object_detail_external_relation_and_missing_object(analyzed):
    with analyzed.db.session() as s:
        ov = analyzed.files.overrides()
        sync = object_detail(s, ov, "Sales|crm.usp_syncfromcrm")
        assert any(r.get("ext") for r in sync["uses"]), "relacja do systemu zewnętrznego"
        assert sync["issues"] and sync["issues"][0]["status"] == "open"
        assert object_detail(s, ov, "Sales|nie.istnieje") is None


@requires_engine
def test_impact_is_transitive_and_limited_by_depth(analyzed):
    with analyzed.db.session() as s:
        ov = analyzed.files.overrides()
        full = impact(s, ov, ORDERS, depth=5)
        shallow = impact(s, ov, ORDERS, depth=1)

    assert {i["depth"] for i in shallow} == {1}
    assert len(full) > len(shallow)
    monthly = next(i for i in full if i["id"] == "Sales|rpt.usp_monthlykpi")
    assert monthly["depth"] == 2
    assert monthly["via"] != ORDERS


# ---------------------------------------------------------------------------
# Katalog obiektów dla Claude'a
# ---------------------------------------------------------------------------


@requires_engine
@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({}, 67),
        ({"project": "sales"}, 45),
        ({"schema": "SALES"}, 12),
        ({"type": "P"}, None),
        ({"search": "order"}, None),
        ({"unassigned": True}, 67),
        ({"undescribed": True}, 67),
        ({"domain": "auto|Sales|crm"}, None),
    ],
)
def test_catalog_filters(analyzed, filters, expected):
    with analyzed.db.session() as s:
        result = list_objects(s, analyzed.files.overrides(), filters, 0, 500)
    if expected is not None:
        assert result["total"] == expected
    assert result["total"] == len(result["items"]) > 0
    if "type" in filters:
        assert all(i["t"] == "P" for i in result["items"])
    if "search" in filters:
        assert all("order" in i["id"] for i in result["items"])


@requires_engine
def test_catalog_pagination_and_compact_items(analyzed):
    analyzed.files.set_override("objects", ORDERS, "description", "Od użytkownika")
    with analyzed.db.session() as s:
        first = list_objects(s, analyzed.files.overrides(), {}, 0, 10)
        last = list_objects(s, analyzed.files.overrides(), {}, 60, 10)
        orders = list_objects(s, analyzed.files.overrides(), {"search": "sales.orders"}, 0, 10)["items"][0]

    assert first["hint"] == "Kolejna partia: offset=10"
    assert (len(last["items"]), last["hint"]) == (7, "To ostatnia partia.")
    assert orders["columns"].startswith("OrderId*")
    assert orders["userLocked"] == ["description"]
    assert orders["description"] == "Od użytkownika"
    assert orders["usedByCount"] > 0
    assert all(v is not None for v in orders.values())


# ---------------------------------------------------------------------------
# Pozycje do wyjaśnienia
# ---------------------------------------------------------------------------


@requires_engine
def test_issues_view(analyzed):
    with analyzed.db.session() as s:
        items = issues_view(s, analyzed.files.overrides())

    kinds = {i["kind"] for i in items}
    assert kinds == {"dynamic", "unresolved", "external"}
    crm = next(i for i in items if i["extId"] == "ext|crm")
    assert crm["extNote"] == {"description": None, "origin": None, "name": None}
    assert all(i["status"] == "open" and i["proposal"] is None for i in items)
    assert items == sorted(items, key=lambda i: (i["kind"], i["objectId"]))


# ---------------------------------------------------------------------------
# Zmiany
# ---------------------------------------------------------------------------


@requires_engine
def test_changeset_view_sorted_by_risk(refreshed):
    with refreshed.db.session() as s:
        view = changeset_view(s, refreshed.files.overrides())
        assert changeset_view(s, refreshed.files.overrides(), 999) is None
        assert find_changeset(s, view["id"]).id == view["id"]

    order = {"high": 0, "med": 1, "low": 2}
    risks = [order[c["risk"]] for c in view["changes"]]
    assert risks == sorted(risks)
    renamed = next(c for c in view["changes"] if c["type"] == "renamed")
    assert (renamed["name"], renamed["oldName"]) == ("pay.PaymentTransactions", "pay.Transactions")
    assert view["from"]["id"] < view["to"]["id"]

    orders = next(c for c in view["changes"] if c["objectId"] == "Sales|sales.orders")
    assert orders["diff"][0][0] == "h"
    assert any(kind == "d" and "DiscountCode" in text for kind, text in orders["diff"])
    assert "commit" not in orders


@requires_engine
def test_changeset_for_claude_is_compact(refreshed):
    with refreshed.db.session() as s:
        result = changeset_for_claude(s, refreshed.files.overrides(), find_changeset(s).id)

    assert "commitList" not in result["stats"]
    assert len(result["commits"]) == 7
    assert all(isinstance(c, str) for c in result["commits"])
    assert all("diff" not in c for c in result["changes"]), "różnice definicji tylko w UI"
    # puste szczegóły są pomijane
    refund = next(c for c in result["changes"] if c["object"] == "Sales|pay.usp_refund")
    assert "columnsRemoved" not in refund and "oldName" not in refund
    assert result["next"].startswith("Przejrzyj zmiany")


def test_no_changeset(db):
    from sqlproj_atlas.core.project_files import Overrides

    with db.session() as s:
        assert find_changeset(s) is None
        assert changeset_view(s, Overrides()) is None


# ---------------------------------------------------------------------------
# Podsumowanie analizy i stan projektu
# ---------------------------------------------------------------------------


@requires_engine
def test_analysis_summary(analyzed):
    with analyzed.db.session() as s:
        sid = s.query(Snapshot).first().id
        save_domains(s, [DomainInput(project="Sales", name="X")])
        summary = analysis_summary(s, analyzed.files.overrides(), sid, {"excludeProjects": ["Tests"]})

    by_name = {p["name"]: p for p in summary["projects"]}
    assert summary["excludedProjects"] == ["Tests"]
    assert summary["excludedSchemas"] == []
    assert by_name["DWH"]["format"] == "klasyczny"
    assert by_name["DWH"]["references"][0] == {
        "kind": "project",
        "name": "Staging",
        "variable": "Staging",
        "literal": None,
    }
    assert (
        list(by_name["Sales"]["schemas"].values())[0]["count"]
        >= list(by_name["Sales"]["schemas"].values())[-1]["count"]
    )
    assert summary["namingPrefixes"]["usp_"]["types"]["P"] >= 10
    assert summary["totals"]["objects"] == 67


@requires_engine
def test_project_status_after_analysis(analyzed):
    status = project_status(analyzed)
    assert status["initialized"] is True
    assert status["newCommitsSinceAnalysis"] == 0
    assert status["uncommittedChanges"] is False
    assert status["objects"] == 67
    assert status["objectsInAutoDomains"] == 67
    assert status["lastAnalysis"]["shortSha"] == status["lastAnalysis"]["sha"][:7]


@requires_engine
def test_project_status_with_missing_analysis_commit(analyzed):
    with analyzed.db.session() as s:
        s.query(Snapshot).update({Snapshot.commit_sha: "0" * 40})
    assert project_status(analyzed)["newCommitsSinceAnalysis"] == 0


def test_project_status_before_analysis(workspace):
    status = project_status(workspace)
    assert status["initialized"] is False
    assert status["lastAnalysis"] is None
    assert len(status["sqlProjectsFound"]) == 4
    assert status["ui"] is None


@requires_engine
def test_map_model_recreates_missing_auto_domain(analyzed):
    from sqlproj_atlas.core.models import Domain

    with analyzed.db.session() as s:
        s.query(Domain).filter_by(id="auto|Sales|crm").delete()
    with analyzed.db.session() as s:
        crm = next(d for d in map_model(s, analyzed.files.overrides())["domains"] if d["id"] == "auto|Sales|crm")
    assert (crm["name"], crm["origin"], crm["project"]) == ("crm", "auto", "Sales")


@requires_engine
def test_object_detail_with_relation_to_unknown_target(analyzed):
    with analyzed.files.edit_overrides() as ov:
        ov.add_edge(ORDERS, "ext|nieznany", "reads", "relacja do systemu spoza analizy")
    with analyzed.db.session() as s:
        uses = object_detail(s, analyzed.files.overrides(), ORDERS)["uses"]
    assert {"id": "ext|nieznany", "name": "ext|nieznany", "kind": "reads", "origin": "user", "unsure": False} in uses
