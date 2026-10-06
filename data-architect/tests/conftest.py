"""Wspólne fixtury testów.

Projekt testowy „Sklep” (3 bazy + projekt testów, 15 commitów) generuje tests/fixtures/make_test_project.py.

Analiza silnikiem trwa kilka sekund, dlatego projekty „po analizie” liczymy raz na sesję testów (szablony),
a każdy test dostaje własną kopię katalogu z bazą. Testy nie wpływają więc na siebie nawzajem.

    project_a    świeży projekt w fazie A (bez analizy)
    workspace    Workspace na świeżym projekcie A (bez analizy)
    analyzed     Workspace po analizie fazy A (projekt Tests pominięty w config.json)
    refreshed    Workspace po analizie A, dopisaniu commitów fazy B i odświeżeniu (zestaw zmian)
    db           pusta baza SQLite do testów na danych syntetycznych (tests/builders.py)
"""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from data_architect.change_analysis.refresh import refresh
from data_architect.core.database import Database
from data_architect.core.workspace import Workspace
from data_architect.project_analysis.engine import engine_status
from data_architect.project_analysis.pipeline import analyze

from make_test_project import make  # tests/fixtures jest na ścieżce (pythonpath w pyproject.toml)

ENGINE_OK = engine_status()["ok"]
requires_engine = pytest.mark.skipif(not ENGINE_OK, reason="brak silnika .NET (zob. /ssdt-atlas:setup)")


# ---------------------------------------------------------------------------
# Projekty bez analizy
# ---------------------------------------------------------------------------


@pytest.fixture
def project_a(tmp_path: Path) -> Path:
    """Świeży projekt testowy w fazie A (stan przed odświeżeniem)."""
    return make(tmp_path / "sklep", "A")


@pytest.fixture
def workspace(project_a: Path) -> Iterator[Workspace]:
    ws = Workspace(project_a)
    yield ws
    ws.close()


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "test.db")
    yield database
    database.dispose()


# ---------------------------------------------------------------------------
# Projekty po analizie (szablony na sesję, kopie na test)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def analyzed_template(tmp_path_factory) -> Path:
    if not ENGINE_OK:
        pytest.skip("brak silnika .NET")

    root = make(tmp_path_factory.mktemp("template-a") / "sklep", "A")
    ws = Workspace(root)
    ws.files.save_config({"excludeProjects": ["Tests"]})
    analyze(ws)
    ws.close()
    return root


@pytest.fixture(scope="session")
def refreshed_template(analyzed_template: Path, tmp_path_factory) -> Path:
    root = _copy(analyzed_template, tmp_path_factory.mktemp("template-b") / "sklep")
    make(root, "B")
    ws = Workspace(root)
    refresh(ws)
    ws.close()
    return root


@pytest.fixture
def analyzed(analyzed_template: Path, tmp_path: Path) -> Iterator[Workspace]:
    ws = Workspace(_copy(analyzed_template, tmp_path / "sklep"))
    yield ws
    ws.close()


@pytest.fixture
def refreshed(refreshed_template: Path, tmp_path: Path) -> Iterator[Workspace]:
    ws = Workspace(_copy(refreshed_template, tmp_path / "sklep"))
    yield ws
    ws.close()


def _copy(source: Path, target: Path) -> Path:
    shutil.copytree(source, target)
    return target
