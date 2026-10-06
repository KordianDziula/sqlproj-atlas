"""Silnik (project_analysis/engine.py): wyszukiwanie .NET i SDK, budowa silnika, stan, błędy uruchomienia, wynik."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from data_architect.core.errors import AtlasError
from data_architect.project_analysis import engine
from data_architect.project_analysis.engine import EngineResult

from conftest import requires_engine

SDK_OK = engine.find_dotnet_sdk() is not None

WINDOWS = sys.platform == "win32"


def fake_program(tmp_path: Path, stdout: str = "", stderr: str = "", exit_code: int = 0) -> Path:
    """Program udający dotnet: wypisuje podany tekst i kończy się podanym kodem."""
    if WINDOWS:
        path = tmp_path / "fake-dotnet.cmd"
        lines = ["@echo off"]
        if stdout:
            lines.append(f"echo {stdout}")
        if stderr:
            lines.append(f"echo {stderr} 1>&2")
        lines.append(f"exit /b {exit_code}")
        path.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
    else:
        path = tmp_path / "fake-dotnet"
        path.write_text(f"#!/bin/sh\necho '{stdout}'\necho '{stderr}' >&2\nexit {exit_code}\n", encoding="ascii")
        path.chmod(0o755)
    return path


# ---------------------------------------------------------------------------
# Wyszukiwanie .NET
# ---------------------------------------------------------------------------


def test_explicit_dotnet_path_has_priority(monkeypatch, tmp_path):
    program = fake_program(tmp_path)
    monkeypatch.setenv("ATLAS_DOTNET", str(program))
    assert engine.dotnet_candidates()[0] == program
    assert engine.find_dotnet() == program


def test_dotnet_root_is_checked(monkeypatch, tmp_path):
    monkeypatch.delenv("ATLAS_DOTNET", raising=False)
    monkeypatch.setenv("DOTNET_ROOT", str(tmp_path))
    assert tmp_path / ("dotnet.exe" if WINDOWS else "dotnet") in engine.dotnet_candidates()


def test_home_dotnet_is_always_a_candidate(monkeypatch):
    monkeypatch.delenv("ATLAS_DOTNET", raising=False)
    monkeypatch.delenv("DOTNET_ROOT", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert Path.home() / ".dotnet" / ("dotnet.exe" if WINDOWS else "dotnet") in engine.dotnet_candidates()


def test_dotnet_from_path_when_no_candidate_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "dotnet_candidates", lambda: [tmp_path / "nie-ma"])
    monkeypatch.setattr(engine.shutil, "which", lambda name: str(tmp_path / "z-path"))
    assert engine.find_dotnet() == tmp_path / "z-path"

    monkeypatch.setattr(engine.shutil, "which", lambda name: None)
    assert engine.find_dotnet() is None


def fake_dotnet_root(root: Path, *sdks: str) -> Path:
    """Katalog instalacji .NET: plik dotnet i podkatalogi sdk/<wersja>."""
    root.mkdir(parents=True)
    exe = root / ("dotnet.exe" if WINDOWS else "dotnet")
    exe.write_text("", encoding="ascii")
    for version in sdks:
        (root / "sdk" / version).mkdir(parents=True)
    return exe


def test_sdk_versions_only_8_and_newer(tmp_path):
    dotnet = fake_dotnet_root(tmp_path / "dotnet", "6.0.400", "8.0.100", "10.0.401", "NuGetFallbackFolder")
    assert engine.sdk_versions(dotnet) == ["10.0.401", "8.0.100"]
    assert engine.sdk_versions(fake_dotnet_root(tmp_path / "runtime")) == [], "sam runtime, bez SDK"


def test_sdk_is_searched_across_installations(monkeypatch, tmp_path):
    # runtime w pierwszej lokalizacji, SDK dopiero w drugiej (np. Program Files i ~/.dotnet)
    runtime_only = fake_dotnet_root(tmp_path / "a")
    with_sdk = fake_dotnet_root(tmp_path / "b", "10.0.100")
    monkeypatch.setattr(engine, "dotnet_candidates", lambda: [runtime_only, with_sdk])
    monkeypatch.setattr(engine.shutil, "which", lambda name: None)
    assert engine.find_dotnet() == runtime_only
    assert engine.find_dotnet_sdk() == with_sdk

    monkeypatch.setattr(engine, "dotnet_candidates", lambda: [runtime_only])
    assert engine.find_dotnet_sdk() is None

    monkeypatch.setattr(engine.shutil, "which", lambda name: str(with_sdk))
    assert engine.find_dotnet_sdk() == with_sdk, "SDK z PATH"


@pytest.mark.skipif(WINDOWS, reason="Homebrew i /usr/local/bin tylko na macOS / Linuksie")
def test_homebrew_dotnet_is_a_candidate(monkeypatch):
    monkeypatch.delenv("ATLAS_DOTNET", raising=False)
    monkeypatch.delenv("DOTNET_ROOT", raising=False)
    assert Path("/opt/homebrew/bin/dotnet") in engine.dotnet_candidates()


# ---------------------------------------------------------------------------
# Budowa silnika
# ---------------------------------------------------------------------------


@pytest.fixture
def engine_source(monkeypatch, tmp_path):
    """Kopia kodu silnika i pusty katalog skompilowanych silników."""
    source = tmp_path / "engine-src"
    shutil.copytree(engine.ENGINE_SOURCE, source, ignore=shutil.ignore_patterns("bin", "obj", "dist"))
    monkeypatch.setattr(engine, "ENGINE_SOURCE", source)
    monkeypatch.setenv("ATLAS_ENGINE_HOME", str(tmp_path / "engines"))
    return source


def test_source_hash_ignores_line_endings_but_not_code(engine_source):
    before = engine.engine_source_hash()
    program = engine_source / "Program.cs"

    program.write_bytes(program.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert engine.engine_source_hash() == before, "Windows (CRLF) i macOS (LF) dostają ten sam silnik"

    program.write_text(program.read_text(encoding="utf-8") + "\n// zmiana\n", encoding="utf-8")
    assert engine.engine_source_hash() != before


def test_engine_dll_lives_in_engine_home(engine_source, tmp_path):
    assert engine.engine_dll() == tmp_path / "engines" / engine.engine_source_hash() / "atlas-engine.dll"


def fake_publish(calls: list, produce: bool = True):
    """Udaje `dotnet publish`: zapisuje wywołanie i tworzy pliki wyniku w katalogu z opcji -o."""

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if produce:
            out = Path(command[command.index("-o") + 1])
            out.mkdir(parents=True)
            (out / "atlas-engine.dll").write_text("dll", encoding="ascii")
            (out / "atlas-engine.pdb").write_text("pdb", encoding="ascii")
        return subprocess.CompletedProcess(command, 0 if produce else 1, "build\nerror CS1002: ; expected", "")

    return run


def test_build_engine_compiles_outside_plugin_folder(engine_source, monkeypatch, tmp_path):
    sdk = fake_dotnet_root(tmp_path / "dotnet", "10.0.100")
    monkeypatch.setattr(engine, "find_dotnet_sdk", lambda: sdk)
    calls = []
    monkeypatch.setattr(engine.subprocess, "run", fake_publish(calls))

    dll = engine.build_engine()

    assert dll == engine.engine_dll() and dll.exists()
    assert not list(dll.parent.glob("*.pdb")), "bez symboli debugowania"
    command, kwargs = calls[0]
    assert command[:2] == [str(sdk), "publish"]
    assert Path(command[2]).parent != engine_source, "kompilacja z kopii kodu, nie z katalogu wtyczki"
    assert kwargs["env"]["DOTNET_CLI_TELEMETRY_OPTOUT"] == "1"
    assert sorted(p.name for p in dll.parent.parent.iterdir()) == [dll.parent.name], "bez katalogów tymczasowych"
    assert not (engine_source / "bin").exists() and not (engine_source / "obj").exists()

    assert engine.build_engine() == dll
    assert len(calls) == 1, "silnik dla tego kodu już jest: bez ponownej kompilacji"


def test_build_engine_without_sdk(engine_source, monkeypatch):
    monkeypatch.setattr(engine, "find_dotnet_sdk", lambda: None)
    with pytest.raises(AtlasError, match="Nie znaleziono .NET SDK 8"):
        engine.build_engine()


def test_build_failure_shows_compiler_output(engine_source, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "find_dotnet_sdk", lambda: fake_dotnet_root(tmp_path / "dotnet", "8.0.100"))
    monkeypatch.setattr(engine.subprocess, "run", fake_publish([], produce=False))

    with pytest.raises(AtlasError, match="CS1002") as error:
        engine.build_engine()
    assert "kod 1" in str(error.value)
    assert not engine.engine_dll().exists()
    assert list((tmp_path / "engines").iterdir()) == [], "nieudana kompilacja nie zostawia plików"


def test_build_timeout(engine_source, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "find_dotnet_sdk", lambda: fake_dotnet_root(tmp_path / "dotnet", "8.0.100"))

    def hang(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(engine.subprocess, "run", hang)
    with pytest.raises(AtlasError, match="Kompilacja silnika nie powiodła się"):
        engine.build_engine()


def test_parallel_build_in_other_session(engine_source, monkeypatch, tmp_path):
    # inna sesja skończyła kompilację tego samego kodu w trakcie naszej: przyjmujemy jej wynik
    monkeypatch.setattr(engine, "find_dotnet_sdk", lambda: fake_dotnet_root(tmp_path / "dotnet", "8.0.100"))
    publish = fake_publish([])

    def publish_while_other_session_finishes(command, **kwargs):
        result = publish(command, **kwargs)
        other = engine.engine_dll()
        other.parent.mkdir(parents=True)
        other.write_text("z innej sesji", encoding="ascii")
        return result

    monkeypatch.setattr(engine.subprocess, "run", publish_while_other_session_finishes)
    assert engine.build_engine().read_text(encoding="ascii") == "z innej sesji"


def test_failed_rename_without_result_is_reported(engine_source, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "find_dotnet_sdk", lambda: fake_dotnet_root(tmp_path / "dotnet", "8.0.100"))
    monkeypatch.setattr(engine.subprocess, "run", fake_publish([]))

    def locked(self, target):
        raise PermissionError("katalog zablokowany")

    monkeypatch.setattr(Path, "rename", locked)
    with pytest.raises(PermissionError):
        engine.build_engine()


@pytest.mark.skipif(not SDK_OK, reason="brak .NET SDK 8+")
def test_real_build_produces_working_engine(engine_source):
    # prawdziwy `dotnet publish` z kopii kodu (pakiety NuGet zwykle są już w pamięci podręcznej)
    dll = engine.build_engine()
    status = engine.engine_status()
    assert status["ok"] is True, status["error"]
    assert status["dll"] == str(dll)


# ---------------------------------------------------------------------------
# Stan silnika
# ---------------------------------------------------------------------------


def test_status_without_built_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "engine_dll", lambda: tmp_path / "brak.dll")
    status = engine.engine_status()
    assert status["ok"] is False
    assert "nie jest jeszcze zbudowany" in status["error"] and "/ssdt-atlas:setup" in status["error"]


def test_status_without_dotnet_lists_checked_places(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "find_dotnet", lambda: None)
    monkeypatch.setattr(engine, "dotnet_candidates", lambda: [tmp_path / "a", tmp_path / "b"])
    error = engine.engine_status()["error"]
    assert "Nie znaleziono .NET" in error and "/ssdt-atlas:setup" in error
    assert str(tmp_path / "a") in error and str(tmp_path / "b") in error


def test_status_when_engine_fails(monkeypatch, tmp_path):
    (tmp_path / "atlas-engine.dll").write_text("", encoding="ascii")
    monkeypatch.setattr(engine, "engine_dll", lambda: tmp_path / "atlas-engine.dll")
    monkeypatch.setattr(engine, "find_dotnet", lambda: fake_program(tmp_path, stderr="zepsuty-runtime", exit_code=3))
    status = engine.engine_status()
    assert status["ok"] is False
    assert status["error"].startswith("Silnik nie uruchomił się: atlas-engine: zepsuty-runtime")


def test_status_when_engine_prints_garbage(monkeypatch, tmp_path):
    (tmp_path / "atlas-engine.dll").write_text("", encoding="ascii")
    monkeypatch.setattr(engine, "engine_dll", lambda: tmp_path / "atlas-engine.dll")
    monkeypatch.setattr(engine, "find_dotnet", lambda: fake_program(tmp_path, stdout="to-nie-json"))
    assert engine.engine_status()["error"].startswith("Silnik nie uruchomił się")


@requires_engine
def test_status_of_real_engine():
    status = engine.engine_status()
    assert status["ok"] is True
    assert set(status["version"]) == {"engine", "dacfx"}


# ---------------------------------------------------------------------------
# Uruchomienie analizy
# ---------------------------------------------------------------------------


def test_run_without_dotnet(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "find_dotnet", lambda: None)
    with pytest.raises(AtlasError, match="Nie znaleziono .NET"):
        engine.run_engine([{"name": "X", "dsp": None, "files": []}], tmp_path)


def test_run_failure_reports_stderr_and_cleans_up(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "find_dotnet", lambda: fake_program(tmp_path, stderr="blad-analizy", exit_code=1))
    work = tmp_path / "work"
    with pytest.raises(AtlasError, match="atlas-engine: blad-analizy"):
        engine.run_engine([{"name": "X", "dsp": None, "files": []}], work)
    assert list(work.iterdir()) == [], "pliki wymiany są usuwane także po błędzie"


def test_run_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "find_dotnet", lambda: fake_program(tmp_path))

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="dotnet", timeout=1)

    monkeypatch.setattr(engine.subprocess, "run", timeout)
    with pytest.raises(AtlasError, match="atlas-engine: Command 'dotnet' timed out"):
        engine.run_engine([{"name": "X", "dsp": None, "files": []}], tmp_path / "work")


@requires_engine
def test_run_real_engine_on_minimal_project(tmp_path):
    files = [
        {
            "path": "t.sql",
            "content": "CREATE TABLE [dbo].[T] ([Id] INT NOT NULL PRIMARY KEY, [Name] NVARCHAR(50) NULL);",
        },
        {"path": "v.sql", "content": "CREATE VIEW [dbo].[V] AS SELECT [Id] FROM [dbo].[T];"},
        {"path": "bad.sql", "content": "CREATE TABLE ((("},
    ]
    result = engine.run_engine([{"name": "Mini", "dsp": None, "files": files}], tmp_path)
    project = result.projects[0]

    assert {o.key for o in project.objects} == {"dbo.t", "dbo.v"}
    table = next(o for o in project.objects if o.key == "dbo.t")
    assert [(c.name, c.pk, c.nullable) for c in table.columns] == [("Id", True, False), ("Name", False, True)]
    assert any(e.source == "dbo.v" and e.to == "dbo.t" and e.kind == "reads" for e in project.edges)
    assert [p.file for p in project.parse_errors] == ["bad.sql"]


# ---------------------------------------------------------------------------
# Model wyniku
# ---------------------------------------------------------------------------


def test_result_model_reads_camel_case_json():
    engine_json = {
        "engine": "1.0",
        "projects": [
            {
                "name": "P",
                "platform": "Sql160",
                "objects": [
                    {
                        "key": "a.b",
                        "schema": "a",
                        "name": "b",
                        "type": "Table",
                        "hash": "h",
                        "columns": [{"name": "Id", "pk": True}],
                    }
                ],
                "edges": [{"from": "a.b", "to": "c.d", "kind": "reads", "externalDb": "$(X)", "externalServer": None}],
                "issues": [],
                "parseErrors": [{"file": "f.sql"}],
                "messages": ["m"],
                "nieznanePole": 1,
            }
        ],
    }
    result = EngineResult.model_validate_json(json.dumps(engine_json))
    project = result.projects[0]
    assert project.objects[0].schema_name == "a"
    assert project.objects[0].columns[0].pk is True
    assert project.edges[0].source == "a.b"
    assert project.edges[0].external_db == "$(X)"
    assert project.parse_errors[0].line == 0


def test_linux_and_macos_locations_are_checked(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    candidates = engine.dotnet_candidates()
    assert Path("/usr/share/dotnet/dotnet") in candidates
    assert Path.home() / ".dotnet" / "dotnet" in candidates
