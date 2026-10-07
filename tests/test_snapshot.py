"""Zapis snapshotu (project_analysis/snapshot.py): interpretacja wyniku silnika i zapis do bazy."""

from sqlalchemy import func, select

from sqlproj_atlas.core.models import DbObject, Definition, Edge, External, Issue, Project, Snapshot
from sqlproj_atlas.project_analysis.engine import EngineProject
from sqlproj_atlas.project_analysis.snapshot import SnapshotMeta, _collect, save_snapshot
from sqlproj_atlas.project_analysis.sqlproj import ProjectReference, SqlProject


def sql_project(name: str, references: list[ProjectReference] = ()) -> SqlProject:
    return SqlProject(
        name=name,
        file=f"{name}/{name}.sqlproj",
        dir=name,
        format="sdk",
        dsp=None,
        files=[],
        references=list(references),
    )


def engine_project(name: str, objects=(), edges=(), issues=(), parse_errors=()) -> EngineProject:
    return EngineProject.model_validate(
        {
            "name": name,
            "objects": list(objects),
            "edges": list(edges),
            "issues": list(issues),
            "parseErrors": list(parse_errors),
        }
    )


def engine_object(key: str, type: str = "Table", hash: str | None = None) -> dict:  # noqa: A002
    schema, name = key.split(".")
    return {
        "key": key,
        "schema": schema,
        "name": name,
        "type": type,
        "hash": hash or f"h-{key}",
        "definition": f"-- {key}",
    }


DWH = sql_project(
    "DWH", [ProjectReference(kind="project", path="Staging/Staging.sqlproj", name="Staging", variable="Staging")]
)
STAGING = sql_project("Staging")
STAGING_ENGINE = engine_project("Staging", objects=[engine_object("stg.orders")])


def collect(dwh_edges=(), dwh_issues=(), parse_errors=(), excluded=frozenset()):
    dwh = engine_project("DWH", objects=[engine_object("dw.load"), engine_object("dw.fact")],
                         edges=dwh_edges, issues=dwh_issues, parse_errors=parse_errors)  # fmt: skip
    return _collect([DWH, STAGING], [dwh, STAGING_ENGINE], excluded)


# ---------------------------------------------------------------------------
# Relacje i odwołania między bazami
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Pominięte schematy (config.json: excludeSchemas)
# ---------------------------------------------------------------------------


def test_excluded_schema_in_every_database():
    rows = collect(
        dwh_edges=[
            {"from": "dw.load", "to": "dw.fact", "kind": "writes"},
            {"from": "dw.load", "to": "stg.orders", "kind": "reads", "externalDb": "$(Staging)"},
        ],
        dwh_issues=[{"object": "dw.load", "kind": "dynamic", "ref": "EXEC(@sql)", "line": 3}],
        excluded={"stg"},
    )
    assert {o["id"] for o in rows.objects} == {"DWH|dw.load", "DWH|dw.fact"}
    assert set(rows.edges) == {("DWH|dw.load", "DWH|dw.fact", "writes")}
    assert rows.issues.keys() == {"DWH|dw.load|dynamic|EXEC(@sql)"}, "odwołanie do pominiętego schematu to nie błąd"


def test_excluded_schema_drops_its_objects_edges_and_issues():
    rows = collect(
        dwh_edges=[
            {"from": "dw.load", "to": "dw.fact", "kind": "writes"},
            {"from": "dw.load", "to": "stg.orders", "kind": "reads", "externalDb": "$(Staging)"},
        ],
        dwh_issues=[{"object": "dw.load", "kind": "dynamic", "ref": "EXEC(@sql)", "line": 3}],
        excluded={"dwh.dw"},
    )
    assert {o["id"] for o in rows.objects} == {"Staging|stg.orders"}
    assert rows.edges == {} and rows.issues == {}
    assert rows.excluded == {"DWH": {"dw": 2}}, "pominięte obiekty liczone według schematu (dla mapy)"


def test_excluded_schema_only_in_named_database():
    rows = collect(
        dwh_edges=[{"from": "dw.load", "to": "stg.orders", "kind": "reads", "externalDb": "$(Staging)"}],
        excluded={"staging.stg"},
    )
    assert "Staging|stg.orders" not in {o["id"] for o in rows.objects}
    assert {o["id"] for o in rows.objects} == {"DWH|dw.load", "DWH|dw.fact"}
    assert rows.edges == {}

    other = collect(excluded={"dwh.stg"})
    assert "Staging|stg.orders" in {o["id"] for o in other.objects}, "schemat stg pominięty tylko w DWH"


def test_excluded_target_inside_one_database():
    rows = collect(dwh_edges=[{"from": "dw.load", "to": "dw.fact", "kind": "writes"}], excluded={"dwh.dw"})
    assert rows.edges == {}


def test_edge_inside_one_database():
    rows = collect(dwh_edges=[{"from": "dw.load", "to": "dw.fact", "kind": "writes"}])
    assert ("DWH|dw.load", "DWH|dw.fact", "writes") in rows.edges


def test_edge_to_other_database_in_solution_via_sqlcmd_variable():
    rows = collect(dwh_edges=[{"from": "dw.load", "to": "stg.orders", "kind": "reads", "externalDb": "$(Staging)"}])
    assert ("DWH|dw.load", "Staging|stg.orders", "reads") in rows.edges
    assert rows.issues == {}


def test_missing_object_in_other_database_is_unresolved():
    rows = collect(dwh_edges=[{"from": "dw.load", "to": "stg.missing", "kind": "reads", "externalDb": "$(Staging)"}])
    issue = rows.issues["DWH|dw.load|unresolved|Staging.stg.missing"]
    assert issue["message"] == "Obiekt nie istnieje w bazie Staging"
    assert not any(dst == "Staging|stg.missing" for _, dst, _ in rows.edges)


def test_system_database_is_skipped():
    rows = collect(dwh_edges=[{"from": "dw.load", "to": "sys.objects", "kind": "reads", "externalDb": "master"}])
    assert rows.edges == {} and rows.issues == {} and rows.externals == {}


def test_database_outside_solution_becomes_external_system():
    rows = collect(dwh_edges=[{"from": "dw.load", "to": "dbo.customers", "kind": "reads", "externalDb": "ErpDb"}])
    assert rows.externals["ext|erpdb"] == {"id": "ext|erpdb", "name": "ErpDb", "kind": "database"}
    assert ("DWH|dw.load", "ext|erpdb", "reads") in rows.edges
    issue = rows.issues["DWH|dw.load|external|ErpDb"]
    assert issue["ext_id"] == "ext|erpdb"
    assert issue["message"] == "Odwołanie do bazy spoza solucji: ErpDb (dbo.customers)"


def test_sqlcmd_variable_without_project_becomes_external_system():
    rows = collect(dwh_edges=[{"from": "dw.load", "to": "dbo.x", "kind": "reads", "externalDb": "$(Archive)"}])
    assert rows.externals["ext|archive"]["kind"] == "variable"
    assert rows.issues["DWH|dw.load|external|Archive"]["message"].startswith("Zmienna SQLCMD $(Archive)")


def test_linked_server_becomes_external_system():
    edge = {"from": "dw.load", "to": "dbo.x", "kind": "reads", "externalDb": "Crm", "externalServer": "CRM_LINK"}
    rows = collect(dwh_edges=[edge])
    assert rows.externals["ext|crm_link"]["kind"] == "linked-server"


def test_duplicate_edges_and_issues_are_stored_once():
    edge = {"from": "dw.load", "to": "dbo.x", "kind": "reads", "externalDb": "ErpDb"}
    rows = collect(dwh_edges=[edge, edge])
    assert len(rows.edges) == 1
    assert len(rows.issues) == 1


# ---------------------------------------------------------------------------
# Problemy zgłoszone przez silnik
# ---------------------------------------------------------------------------


def test_openquery_becomes_read_from_linked_server():
    openquery = {
        "object": "dw.load",
        "kind": "openquery",
        "ref": "CRM",
        "server": "CRM",
        "line": 7,
        "snippet": "OPENQUERY(CRM, ...)",
    }
    rows = collect(dwh_issues=[openquery])
    assert ("DWH|dw.load", "ext|crm", "reads") in rows.edges
    issue = rows.issues["DWH|dw.load|external|CRM"]
    assert (issue["line"], issue["snippet"], issue["message"]) == (
        7,
        "OPENQUERY(CRM, ...)",
        "OPENQUERY do serwera CRM:",
    )


def test_dynamic_sql_issue_is_kept_as_is():
    rows = collect(
        dwh_issues=[
            {"object": "dw.load", "kind": "dynamic", "ref": "EXEC(@sql)", "line": 3, "message": "Dynamiczny SQL"}
        ]
    )
    issue = rows.issues["DWH|dw.load|dynamic|EXEC(@sql)"]
    assert (issue["kind"], issue["line"], issue["message"]) == ("dynamic", 3, "Dynamiczny SQL")


def test_parse_error_becomes_issue_of_the_file():
    rows = collect(parse_errors=[{"file": "DWH/bad.sql", "line": 2, "message": "Incorrect syntax"}])
    issue = rows.issues["DWH|file:DWH/bad.sql|parse|DWH/bad.sql"]
    assert (issue["kind"], issue["line"], issue["message"]) == ("parse", 2, "Incorrect syntax")


# ---------------------------------------------------------------------------
# Zapis do bazy
# ---------------------------------------------------------------------------


def test_save_snapshot_stores_all_rows_and_statistics(db):
    dwh = engine_project(
        "DWH",
        objects=[engine_object("dw.load", "Procedure"), engine_object("dw.fact")],
        edges=[
            {"from": "dw.load", "to": "dw.fact", "kind": "writes"},
            {"from": "dw.load", "to": "x.y", "kind": "reads", "externalDb": "ErpDb"},
        ],
    )
    meta = SnapshotMeta(
        commit_sha="abc", commit_date="2026-10-01", author="Anna", message="init", dirty=True, label="t"
    )

    with db.session() as s:
        sid = save_snapshot(s, meta, [DWH], [dwh])

    with db.session() as s:
        snapshot = s.get(Snapshot, sid)
        assert (snapshot.commit_sha, snapshot.dirty, snapshot.label) == ("abc", 1, "t")
        assert snapshot.stats["projects"]["DWH"] == {"total": 2, "tables": 1, "code": 1, "byType": {"P": 1, "T": 1}}
        assert s.scalar(select(func.count()).select_from(DbObject)) == 2
        assert s.scalar(select(func.count()).select_from(Edge)) == 2
        assert s.scalar(select(func.count()).select_from(Issue)) == 1
        assert s.scalar(select(func.count()).select_from(External)) == 1
        assert s.get(Project, (sid, "DWH")).info["files"] == 0


def test_definitions_are_shared_between_snapshots(db):
    dwh = engine_project("DWH", objects=[engine_object("dw.fact", hash="same")])
    meta = SnapshotMeta(commit_sha=None, commit_date="2026-10-01", author=None, message=None)

    with db.session() as s:
        save_snapshot(s, meta, [DWH], [dwh])
        save_snapshot(s, meta, [DWH], [dwh])
        assert s.scalar(select(func.count()).select_from(Definition)) == 1
        assert s.scalar(select(func.count()).select_from(Snapshot)) == 2


def test_empty_analysis_is_saved(db):
    meta = SnapshotMeta(commit_sha=None, commit_date="2026-10-01", author=None, message=None)
    with db.session() as s:
        sid = save_snapshot(s, meta, [], [])
        assert s.get(Snapshot, sid).stats == {
            "objects": 0, "edges": 0, "issues": 0, "projects": {},
            "excluded": {"projects": [], "schemas": {}},
        }  # fmt: skip
