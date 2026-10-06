"""Uruchamianie tak jak robi to Claude Code: launcher bin/atlas, hak SessionStart (check), okrojone środowisko."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from typer.testing import CliRunner

from data_architect import cli
from data_architect.core.settings import PLUGIN_ROOT

WINDOWS = sys.platform == "win32"
runner = CliRunner()


def test_check_is_silent_when_engine_works(project_a):
    with mock.patch("data_architect.project_analysis.engine.engine_status", return_value={"ok": True, "error": None}):
        result = runner.invoke(cli.app, ["--project", str(project_a), "check"])
    assert result.exit_code == 0
    assert result.output == ""


def test_check_suggests_setup_when_engine_missing(project_a):
    missing = {"ok": False, "error": "Nie znaleziono .NET (wymagany .NET SDK 8 lub nowszy do zbudowania silnika)."}
    with mock.patch("data_architect.project_analysis.engine.engine_status", return_value=missing):
        result = runner.invoke(cli.app, ["--project", str(project_a), "check"])
        outside = runner.invoke(cli.app, ["--project", str(project_a / "src" / "Sales" / "crm"), "check"])

    assert "Nie znaleziono .NET" in result.output
    assert "/ssdt-atlas:setup" in result.output
    assert outside.output == "", "poza projektem SSDT hak milczy"


def _launcher() -> list[str]:
    """Launcher tak, jak uruchamia go Claude Code; korzysta ze wspólnego środowiska (bez pakietów deweloperskich)."""
    venv = Path(os.environ.get("ATLAS_VENV") or Path.home() / ".data-architect" / "venv")
    if not venv.exists():
        pytest.skip("środowisko launchera nieprzygotowane (bin/atlas install)")
    return [str(PLUGIN_ROOT / "bin" / "atlas.cmd")] if WINDOWS else ["sh", str(PLUGIN_ROOT / "bin" / "atlas")]


def test_launcher_runs_doctor():
    result = subprocess.run([*_launcher(), "doctor"], capture_output=True, timeout=300)
    report = json.loads(result.stdout.decode("utf-8"))
    assert report["python"].startswith("3.")


def test_launcher_starts_mcp_server(project_a):
    # wykrywa m.in. brak pakietu w zależnościach uruchomieniowych (testy e2e działają w środowisku deweloperskim)
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
    }
    result = subprocess.run(
        _launcher(),
        input=(json.dumps(initialize) + "\n").encode("utf-8"),
        capture_output=True,
        timeout=300,
        env=os.environ | {"ATLAS_PROJECT_DIR": str(project_a)},
    )
    response = json.loads(result.stdout.decode("utf-8").splitlines()[0])
    assert response["result"]["serverInfo"]["name"] == "ssdt-atlas", result.stderr.decode("utf-8", "replace")


@pytest.mark.skipif(
    not (WINDOWS and (Path.home() / ".dotnet" / "dotnet.exe").exists()), reason="test dla .NET w ~/.dotnet na Windows"
)
def test_dotnet_found_without_environment_variables():
    # Claude Code uruchamia serwer MCP z okrojonym środowiskiem (bez LOCALAPPDATA); .NET z ~/.dotnet musi się znaleźć
    env = {k: os.environ[k] for k in ("SYSTEMROOT", "USERPROFILE", "HOMEDRIVE", "HOMEPATH") if k in os.environ}
    env["PATH"] = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "system32")
    env["PYTHONPATH"] = str(PLUGIN_ROOT / "src")

    result = subprocess.run(
        [sys.executable, "-m", "data_architect", "doctor"], env=env, capture_output=True, timeout=120
    )
    report = json.loads(result.stdout.decode("utf-8"))
    assert report["engine"]["ok"], report["engine"]["error"]


# ---------------------------------------------------------------------------
# Polecenia doctor i check bez launchera
# ---------------------------------------------------------------------------


def test_doctor_exit_code_follows_engine_state():
    with mock.patch(
        "data_architect.project_analysis.engine.engine_status", return_value={"ok": False, "error": "brak"}
    ):
        failed = runner.invoke(cli.app, ["doctor"])
    with mock.patch("data_architect.project_analysis.engine.engine_status", return_value={"ok": True, "error": None}):
        passed = runner.invoke(cli.app, ["doctor"])

    assert (failed.exit_code, passed.exit_code) == (1, 0)
    assert json.loads(failed.output)["engine"]["error"] == "brak"


def test_doctor_reports_sdk(tmp_path):
    with mock.patch("data_architect.project_analysis.engine.find_dotnet_sdk", return_value=None):
        report = json.loads(runner.invoke(cli.app, ["doctor"]).output)
    assert report["sdk"] is None

    with (
        mock.patch("data_architect.project_analysis.engine.find_dotnet_sdk", return_value=tmp_path / "dotnet"),
        mock.patch("data_architect.project_analysis.engine.sdk_versions", return_value=["10.0.100"]),
    ):
        report = json.loads(runner.invoke(cli.app, ["doctor"]).output)
    assert report["sdk"] == {"dotnet": str(tmp_path / "dotnet"), "versions": ["10.0.100"]}


def test_build_engine_command(tmp_path):
    with mock.patch("data_architect.project_analysis.engine.build_engine", return_value=tmp_path / "atlas-engine.dll"):
        ok = runner.invoke(cli.app, ["build-engine"])
    assert ok.exit_code == 0
    assert ok.output.startswith("Silnik gotowy:")

    from data_architect.core.errors import AtlasError

    with mock.patch("data_architect.project_analysis.engine.build_engine", side_effect=AtlasError("brak SDK")):
        failed = runner.invoke(cli.app, ["build-engine"])
    assert failed.exit_code == 1
    assert "brak SDK" in failed.output


def test_sql_project_is_found_only_a_few_levels_deep(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    (deep / "Db.sqlproj").write_text("<Project/>", encoding="utf-8")
    assert cli.has_sql_project(tmp_path) is False
    assert cli.has_sql_project(tmp_path / "a") is True


def test_sql_project_in_technical_folder_is_ignored(tmp_path):
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "Db.sqlproj").write_text("<Project/>", encoding="utf-8")
    assert cli.has_sql_project(tmp_path) is False


def test_unknown_command():
    assert runner.invoke(cli.app, ["nieznane"]).exit_code == 2


def test_ui_command_starts_standalone_app(project_a, monkeypatch):
    started = []
    monkeypatch.setattr(
        "data_architect.web_app.runner.serve_forever", lambda ws, open_in_browser: started.append((ws, open_in_browser))
    )
    result = runner.invoke(cli.app, ["--project", str(project_a), "ui", "--no-browser"])
    assert result.exit_code == 0
    [(ws, open_in_browser)] = started
    assert (ws.project_dir, open_in_browser) == (project_a.resolve(), False)
    ws.close()
