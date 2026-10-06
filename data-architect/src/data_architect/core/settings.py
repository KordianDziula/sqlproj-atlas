"""Ustawienia wtyczki: ścieżki, stałe i konfiguracja logowania.

Zmienne środowiskowe (wszystkie opcjonalne):
    ATLAS_PROJECT_DIR   katalog analizowanego projektu (Claude Code ustawia go w .mcp.json)
    ATLAS_PLUGIN_ROOT   katalog wtyczki (ustawia launcher bin/atlas)
    ATLAS_DOTNET        ścieżka do dotnet, gdy nie ma go w standardowych miejscach
    ATLAS_ENGINE_HOME   katalog skompilowanych silników (domyślnie ~/.data-architect/engine)
    ATLAS_PORT          stały port aplikacji webowej (domyślnie losowy)
    ATLAS_NO_BROWSER=1  nie otwieraj przeglądarki (testy, praca zdalna)
"""

import logging
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ścieżki
# ---------------------------------------------------------------------------

# src/data_architect/core/settings.py → cztery poziomy wyżej jest katalog wtyczki
PLUGIN_ROOT = Path(os.environ.get("ATLAS_PLUGIN_ROOT") or Path(__file__).parents[3]).resolve()

# pliki aplikacji webowej (index.html, app.js, app.css) należą do komponentu web_app
UI_DIR = Path(__file__).resolve().parents[1] / "web_app" / "static"
# kod źródłowy silnika C#; kompilujemy go na komputerze użytkownika (project_analysis/engine.build_engine)
ENGINE_SOURCE = PLUGIN_ROOT / "engine"

# katalog danych wtyczki w analizowanym projekcie
DATA_DIR_NAME = Path(".claude") / "data-architect"

# katalogi pomijane przy przeszukiwaniu projektu
SKIP_DIRS = {".git", "node_modules", "bin", "obj", ".vs", ".claude", ".idea", "__pycache__", ".venv"}


def resolve_project_dir() -> Path:
    """Katalog projektu użytkownika: zmienna z .mcp.json, a gdy jej brak, bieżący katalog."""
    from_env = os.environ.get("ATLAS_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR")

    # Claude Code zostawia "${CLAUDE_PROJECT_DIR}" dosłownie, gdy zmienna nie jest znana
    if from_env and "${" not in from_env and Path(from_env).exists():
        return Path(from_env).resolve()

    return Path.cwd()


def engine_home() -> Path:
    """Katalog skompilowanych silników (w katalogu domowym, wspólny dla projektów i wersji wtyczki)."""
    return Path(os.environ.get("ATLAS_ENGINE_HOME") or Path.home() / ".data-architect" / "engine")


# ---------------------------------------------------------------------------
# Typy obiektów SQL
# ---------------------------------------------------------------------------

# typ obiektu z DacFx → skrót używany w UI i narzędziach (T, V, P, ...)
TYPE_LETTER = {
    "Table": "T",
    "View": "V",
    "Procedure": "P",
    "ScalarFunction": "F",
    "TableValuedFunction": "F",
    "DmlTrigger": "Tr",
    "Synonym": "Sy",
    "Sequence": "Sq",
    "TableType": "TT",
}


def type_letter(object_type: str) -> str:
    return TYPE_LETTER.get(object_type, "?")


# ---------------------------------------------------------------------------
# Logowanie
# ---------------------------------------------------------------------------


def setup_logging(log_file: Path | None = None) -> None:
    """Log na stderr (stdout należy do protokołu MCP) i opcjonalnie do pliku atlas.log w projekcie."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="[data-architect %(asctime)s] %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
