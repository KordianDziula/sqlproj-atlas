"""Odczyt projektów SSDT (project_analysis/sqlproj.py, references.py).

Pliki modelu, wzorce MSBuild, metadane projektów, kodowanie plików, odwołania między bazami.
"""

from pathlib import Path

import pytest

from ssdt_atlas.project_analysis.references import resolve_database
from ssdt_atlas.project_analysis.sqlproj import (
    ProjectReference,
    SqlProject,
    _glob_match,
    discover_projects,
    list_files,
    parse_refactor_log,
    read_text,
)

SDK = '<Project Sdk="Microsoft.Build.Sql/1.0.0">{body}</Project>'
CLASSIC = '<?xml version="1.0" encoding="utf-8"?><Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">{body}</Project>'


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def project(root: Path, name: str = None) -> SqlProject:
    projects = {p.name: p for p in discover_projects(root)}
    return projects[name] if name else next(iter(projects.values()))


# ---------------------------------------------------------------------------
# Wzorce MSBuild i .refactorlog
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("Scripts/Seed/Currencies.sql", "Scripts\\**", True),
        ("a/b/c.sql", "**/*.sql", True),
        ("dbo/x.sql", "dbo/*.sql", True),
        ("dbo/a/x.sql", "dbo/*.sql", False),
        ("tables/t1.sql", "Tables\\T?.sql", True),
        ("tables/t10.sql", "Tables\\T?.sql", False),
        ("x.sql", "./x.sql", True),
    ],
)
def test_msbuild_globs(path, pattern, expected):
    assert _glob_match(path, pattern) is expected


def test_refactorlog_rename():
    xml = (
        '<Operations><Operation Name="Rename Refactor" Key="k">'
        '<Property Name="ElementName" Value="[pay].[Transactions]" />'
        '<Property Name="ElementType" Value="SqlTable" />'
        '<Property Name="NewName" Value="[PaymentTransactions]" />'
        "</Operation></Operations>"
    )
    assert parse_refactor_log(xml) == [
        {"element": "[pay].[Transactions]", "newName": "[PaymentTransactions]", "type": "SqlTable"}
    ]


def test_refactorlog_ignores_other_operations_and_invalid_xml():
    move = (
        '<Operations><Operation Name="Move Schema">'
        '<Property Name="ElementName" Value="[a].[b]" />'
        "</Operation></Operations>"
    )
    assert parse_refactor_log(move) == []
    assert parse_refactor_log("<Operations><niedomknięte>") == []
    assert parse_refactor_log("") == []


# ---------------------------------------------------------------------------
# Projekt testowy „Sklep”
# ---------------------------------------------------------------------------


def test_discover_projects(project_a):
    by_name = {p.name: p for p in discover_projects(project_a)}

    assert sorted(by_name) == ["DWH", "Sales", "Staging", "Tests"]
    assert by_name["Sales"].format == "sdk"
    assert by_name["Staging"].format == "classic"

    # SDK-style: <Build Remove="Scripts\**"> i skrypty post-deploy są poza modelem bazy
    assert not any("/Scripts/" in f for f in by_name["Sales"].files)
    assert by_name["Sales"].post_deploy[0].endswith("Scripts/Script.PostDeployment.sql")
    assert len(by_name["Sales"].files) == 53
    assert len(by_name["Staging"].files) == 11

    # zmienne SQLCMD i referencje projektu DWH
    assert [v.name for v in by_name["DWH"].sqlcmd] == ["Staging", "Archive", "ErpDb"]
    assert by_name["DWH"].references[0].variable == "Staging"
    assert by_name["Sales"].refactorlog == "src/Sales/Sales.refactorlog"


def test_project_info_for_database(project_a):
    info = project(project_a, "DWH").info()
    assert info["files"] > 0
    assert {"name", "defaultValue", "value"} == set(info["sqlcmd"][0])
    assert info["references"][0]["kind"] == "project"


# ---------------------------------------------------------------------------
# Przypadki brzegowe .sqlproj
# ---------------------------------------------------------------------------


def test_sdk_project_includes_all_sql_except_removed_none_and_deploy(tmp_path):
    body = (
        '<ItemGroup><Build Remove="Archive\\**" /><None Include="Notes.sql" />'
        '<PreDeploy Include="Pre.sql" /><Build Include="..\\Shared\\Common.sql" /></ItemGroup>'
    )
    write(tmp_path, {
        "Db/Db.sqlproj": SDK.format(body=body),
        "Db/dbo/A.sql": "", "Db/Archive/Old.sql": "", "Db/Notes.sql": "", "Db/Pre.sql": "", "Db/readme.txt": "",
        "Shared/Common.sql": "",
    })  # fmt: skip
    assert project(tmp_path).files == ["Db/dbo/A.sql", "Shared/Common.sql"]


def test_classic_project_includes_only_listed_files_and_patterns(tmp_path):
    body = (
        "<ItemGroup>"
        '<Build Include="dbo\\Tables\\A.sql" />'
        '<Build Include="etl\\**\\*.sql" />'
        '<Build Include="brak.sql" />'
        "</ItemGroup>"
    )
    write(tmp_path, {
        "Db.sqlproj": CLASSIC.format(body=body),
        "dbo/Tables/A.sql": "", "dbo/Tables/B.sql": "", "etl/x/Load.sql": "", "etl/Load2.sql": "",
    })  # fmt: skip
    assert project(tmp_path).files == ["dbo/Tables/A.sql", "etl/Load2.sql", "etl/x/Load.sql"]


def test_nested_project_files_belong_to_nested_project(tmp_path):
    write(tmp_path, {
        "Outer.sqlproj": SDK.format(body="<PropertyGroup><Name>Outer</Name></PropertyGroup>"),
        "a.sql": "",
        "inner/Inner.sqlproj": SDK.format(body="<PropertyGroup><Name>Inner</Name></PropertyGroup>"),
        "inner/b.sql": "",
    })  # fmt: skip
    assert project(tmp_path, "Outer").files == ["a.sql"]
    assert project(tmp_path, "Inner").files == ["inner/b.sql"]


def test_project_metadata_and_name_fallback(tmp_path):
    body = (
        "<PropertyGroup><DSP>Microsoft.Data.Tools.Schema.Sql.Sql160DatabaseSchemaProvider</DSP></PropertyGroup>"
        "<ItemGroup>"
        '<SqlCmdVariable Include="Env">'
        "<DefaultValue>dev</DefaultValue>"
        "<Value>$(SqlCmdVar__1)</Value>"
        "</SqlCmdVariable>"
        '<ArtifactReference Include="..\\dacpacs\\master.dacpac">'
        "<DatabaseVariableLiteralValue>master</DatabaseVariableLiteralValue>"
        "</ArtifactReference>"
        '<ProjectReference Include="..\\Other\\Other.sqlproj">'
        "<DatabaseSqlCmdVariable>Other</DatabaseSqlCmdVariable>"
        "</ProjectReference>"
        "</ItemGroup>"
    )
    write(tmp_path, {"Db/Moja Baza.sqlproj": SDK.format(body=body), "Db/Moja.refactorlog": "<Operations/>"})
    p = project(tmp_path)

    assert p.name == "Moja Baza", "bez <Name> nazwą jest nazwa pliku"
    assert p.dsp.endswith("Sql160DatabaseSchemaProvider")
    assert (p.sqlcmd[0].name, p.sqlcmd[0].default_value) == ("Env", "dev")
    assert [(r.kind, r.name, r.variable, r.literal) for r in p.references] == [
        ("project", "Other", "Other", None),
        ("dacpac", "master", None, "master"),
    ]
    assert p.refactorlog == "Db/Moja.refactorlog", "znaleziony w katalogu projektu"


def test_project_file_with_bom_and_polish_path(tmp_path):
    root = tmp_path / "Projekt Żółw"
    write(root, {"Baza Główna/Baza.sqlproj": "﻿" + SDK.format(body=""), "Baza Główna/Tabele/Zamówienia.sql": ""})
    p = project(root)
    assert p.name == "Baza"
    assert p.files == ["Baza Główna/Tabele/Zamówienia.sql"]


def test_invalid_project_file_is_skipped(tmp_path, caplog):
    write(tmp_path, {"Bad/Bad.sqlproj": "<Project><niedomknięte>", "Good/Good.sqlproj": SDK.format(body="")})
    assert [p.name for p in discover_projects(tmp_path)] == ["Good"]
    assert "Pominięto Bad/Bad.sqlproj" in caplog.text


def test_folder_without_projects(tmp_path):
    write(tmp_path, {"readme.md": "", "x.sql": ""})
    assert discover_projects(tmp_path) == []


def test_list_files_skips_technical_folders(tmp_path):
    write(
        tmp_path,
        {"a.sql": "", "bin/b.sql": "", "obj/c.sql": "", ".git/d": "", "node_modules/e.sql": "", "src/f.sql": ""},
    )
    assert list_files(tmp_path) == ["a.sql", "src/f.sql"]


def test_read_text_keeps_line_endings(tmp_path):
    (tmp_path / "a.sql").write_bytes(b"SELECT 1\r\nGO\r\n")
    assert read_text(tmp_path, "a.sql") == "SELECT 1\r\nGO\r\n"


@pytest.mark.parametrize(
    "encoding",
    ["utf-8-sig", "utf-16", "utf-16-be"],
    ids=["UTF-8 z BOM", "UTF-16 LE z BOM", "UTF-16 BE z BOM"],
)
def test_read_text_removes_byte_order_mark(tmp_path, encoding):
    text = "CREATE TABLE [dbo].[Zamówienia] ([Id] INT);\r\n"
    data = text.encode(encoding) if encoding != "utf-16-be" else b"\xfe\xff" + text.encode("utf-16-be")
    (tmp_path / "a.sql").write_bytes(data)
    assert read_text(tmp_path, "a.sql") == text


def test_read_text_replaces_invalid_bytes(tmp_path):
    (tmp_path / "a.sql").write_bytes(b"-- komentarz \xb3\n")  # „ł” w Windows-1250, niepoprawne w UTF-8
    assert read_text(tmp_path, "a.sql") == "-- komentarz \ufffd\n"


# ---------------------------------------------------------------------------
# Odwołania między bazami
# ---------------------------------------------------------------------------


def test_resolve_database_in_test_project(project_a):
    projects = discover_projects(project_a)
    by_name = {p.name: p for p in projects}

    assert resolve_database(by_name["DWH"], "$(Staging)", projects) == {"project": "Staging"}
    assert resolve_database(by_name["DWH"], "$(Archive)", projects) == {"ext": "Archive", "kind": "variable"}
    assert resolve_database(by_name["Sales"], "ImportDb", projects) == {"ext": "ImportDb", "kind": "database"}
    assert resolve_database(by_name["Sales"], "master", projects) == {"system": True}
    assert resolve_database(by_name["Sales"], "Staging", projects) == {"project": "Staging"}


def make_project(name: str, references: list[ProjectReference] = ()) -> SqlProject:
    return SqlProject(
        name=name,
        file=f"{name}/{name}.sqlproj",
        dir=name,
        format="sdk",
        dsp=None,
        files=[],
        references=list(references),
    )


@pytest.mark.parametrize(
    ("reference", "token", "expected"),
    [
        (
            ProjectReference("project", "Gone/Gone.sqlproj", "Gone", variable="Gone"),
            "$(Gone)",
            {"ext": "Gone", "kind": "database"},
        ),
        (ProjectReference("project", "inna/sciezka.sqlproj", "Target", variable="T"), "$(T)", {"project": "Target"}),
        (ProjectReference("dacpac", "x/Erp.dacpac", "Erp", variable="Erp"), "$(Erp)", {"ext": "Erp", "kind": "dacpac"}),
        (ProjectReference("dacpac", "x/msdb.dacpac", "msdb", variable="Msdb"), "$(MSDB)", {"system": True}),
        (
            ProjectReference("project", "Target/Target.sqlproj", "Target", literal="Alias"),
            "alias",
            {"project": "Target"},
        ),
        (ProjectReference("dacpac", "x/master.dacpac", "master", literal="Sys"), "Sys", {"system": True}),
    ],
    ids=[
        "projekt spoza solucji",
        "projekt po nazwie",
        "dacpac",
        "dacpac systemowy",
        "literał projektu",
        "literał dacpac systemowy",
    ],
)
def test_resolve_database_branches(reference, token, expected):
    source = make_project("Source", [reference])
    assert resolve_database(source, token, [source, make_project("Target")]) == expected


def test_resolve_database_without_source_project():
    assert resolve_database(None, "$(X)", []) == {"ext": "X", "kind": "variable"}
    assert resolve_database(None, "TEMPDB", []) == {"system": True}
