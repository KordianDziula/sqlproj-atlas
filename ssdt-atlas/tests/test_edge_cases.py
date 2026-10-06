"""Przypadki brzegowe całego przepływu (analiza prawdziwym silnikiem na nietypowych projektach)."""

from pathlib import Path

import pytest

from ssdt_atlas.architecture.issues_view import issues_view
from ssdt_atlas.architecture.map_view import map_model
from ssdt_atlas.change_analysis.refresh import refresh
from ssdt_atlas.core.workspace import Workspace
from ssdt_atlas.mcp_server.status import project_status
from ssdt_atlas.project_analysis.pipeline import analyze

from conftest import requires_engine

pytestmark = requires_engine

SDK = '<Project Sdk="Microsoft.Build.Sql/1.0.0"><PropertyGroup><Name>{name}</Name></PropertyGroup></Project>'


@pytest.fixture
def new_project(tmp_path):
    """Buduje projekt z podanych plików i zwraca Workspace (zamykany po teście)."""
    created = []

    def build(files: dict[str, str | bytes], folder: str = "projekt") -> Workspace:
        root = tmp_path / folder
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        ws = Workspace(root)
        created.append(ws)
        return ws

    yield build
    for ws in created:
        ws.close()


def model(ws: Workspace) -> dict:
    with ws.db.session() as s:
        return map_model(s, ws.files.overrides())


def test_polish_characters_and_spaces_in_paths(new_project):
    ws = new_project(
        {
            "Baza Główna/Baza Główna.sqlproj": SDK.format(name="Sprzedaż"),
            "Baza Główna/Tabele i widoki/Zamówienia.sql": (
                "CREATE TABLE [dbo].[Zamówienia] ([Id] INT NOT NULL PRIMARY KEY, [Opis] NVARCHAR(50) NULL);"
            ),
        },
        folder="Projekt Żółw ąę",
    )
    analyze(ws)
    result = model(ws)

    assert [p["name"] for p in result["projects"]] == ["Sprzedaż"]
    [obj] = result["objects"]
    assert (obj["id"], obj["name"]) == ("Sprzedaż|dbo.zamówienia", "Zamówienia")


def test_syntax_error_becomes_issue_and_other_files_are_analyzed(new_project):
    ws = new_project(
        {
            "Db/Db.sqlproj": SDK.format(name="Db"),
            "Db/ok.sql": "CREATE TABLE [dbo].[Ok] ([Id] INT NOT NULL);",
            "Db/zly.sql": "CREATE TABLE [dbo].[Zly] ( ((( ;",
        }
    )
    analyze(ws)

    assert [o["name"] for o in model(ws)["objects"]] == ["Ok"]
    with ws.db.session() as s:
        [parse] = [i for i in issues_view(s, ws.files.overrides()) if i["kind"] == "parse"]
    assert parse["ref"] == "Db/zly.sql"
    assert parse["line"] >= 1


def test_project_without_sql_files(new_project):
    ws = new_project({"Empty/Empty.sqlproj": SDK.format(name="Empty")})
    analyze(ws)
    result = model(ws)
    assert result["initialized"] is True
    assert result["objects"] == []
    assert [p["name"] for p in result["projects"]] == ["Empty"]


def test_folder_without_projects(new_project):
    ws = new_project({"readme.md": "brak projektów SSDT"})
    analyze(ws)
    assert model(ws)["projects"] == []
    assert project_status(ws)["sqlProjectsFound"] == []


def test_bom_and_mixed_line_endings(new_project):
    ws = new_project(
        {
            "Db/Db.sqlproj": "﻿" + SDK.format(name="Db"),
            "Db/bom.sql": b"\xef\xbb\xbfCREATE TABLE [dbo].[Bom] ([Id] INT NOT NULL);",
            "Db/lf.sql": b"CREATE VIEW [dbo].[V]\nAS\nSELECT [Id] FROM [dbo].[Bom];",
            "Db/crlf.sql": b"CREATE PROCEDURE [dbo].[P]\r\nAS\r\nSELECT [Id] FROM [dbo].[V];",
        }
    )
    analyze(ws)
    result = model(ws)
    assert sorted(o["name"] for o in result["objects"]) == ["Bom", "P", "V"]
    assert {(e["src"], e["dst"]) for e in result["edges"]} >= {("Db|dbo.v", "Db|dbo.bom"), ("Db|dbo.p", "Db|dbo.v")}


def test_project_without_git(new_project):
    ws = new_project({"Db/Db.sqlproj": SDK.format(name="Db"), "Db/t.sql": "CREATE TABLE [dbo].[T] ([Id] INT);"})
    analyze(ws)
    status = project_status(ws)
    assert status["lastAnalysis"]["sha"] is None
    assert status["uncommittedChanges"] is None

    (ws.project_dir / "Db" / "t2.sql").write_text("CREATE TABLE [dbo].[T2] ([Id] INT);", encoding="utf-8")
    result = refresh(ws)
    assert result["changeset"] is not None


def test_excluded_project_is_not_analyzed(new_project):
    ws = new_project(
        {
            "A/A.sqlproj": SDK.format(name="A"),
            "A/t.sql": "CREATE TABLE [dbo].[T] ([Id] INT);",
            "Tests/Tests.sqlproj": SDK.format(name="Tests"),
            "Tests/x.sql": "CREATE TABLE [dbo].[X] ([Id] INT);",
        }
    )
    ws.files.save_config({"excludeProjects": ["tests"]})
    analyze(ws)
    assert [p["name"] for p in model(ws)["projects"]] == ["A"]
    assert len(project_status(ws)["sqlProjectsFound"]) == 2, "stan pokazuje wszystkie znalezione projekty"


def test_corrupted_overrides_do_not_break_analysis(analyzed):
    analyzed.files.overrides_file.write_text("{ zepsuty", encoding="utf-8")
    assert len(model(analyzed)["objects"]) == 67
    analyzed.files.set_override("objects", "Sales|sales.orders", "description", "Nowy")
    assert analyzed.files.overrides().objects["Sales|sales.orders"].description == "Nowy"


def test_reanalysis_keeps_claude_semantics(analyzed):
    from ssdt_atlas.architecture.editing import AssignItem, DomainInput, assign, save_domains

    with analyzed.db.session() as s:
        save_domains(s, [DomainInput(project="Sales", name="Zamówienia")])
        assign(s, analyzed.files, [AssignItem(object="sales.Orders", domain="Sales|zamowienia", description="Opis")])

    analyze(analyzed)
    orders = next(o for o in model(analyzed)["objects"] if o["id"] == "Sales|sales.orders")
    assert (orders["domain"], orders["described"]) == ("Sales|zamowienia", True)


def test_files_outside_projects_are_ignored(new_project):
    ws = new_project(
        {
            "Db/Db.sqlproj": SDK.format(name="Db"),
            "Db/t.sql": "CREATE TABLE [dbo].[T] ([Id] INT);",
            "scripts/adhoc.sql": "CREATE TABLE [dbo].[Adhoc] ([Id] INT);",
        }
    )
    analyze(ws)
    assert [o["name"] for o in model(ws)["objects"]] == ["T"]


def test_paths_are_stored_relative_with_forward_slashes(analyzed):
    with analyzed.db.session() as s:
        from ssdt_atlas.core.models import DbObject

        files = [o.file for o in s.query(DbObject).limit(20)]
    assert all("\\" not in f and not Path(f).is_absolute() for f in files)
