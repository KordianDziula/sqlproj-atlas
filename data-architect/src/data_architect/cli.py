"""Punkt startowy procesu (Typer): wybiera tryb pracy jednego programu.

    bin/atlas            serwer MCP po stdio (domyślnie; tak uruchamia go Claude Code przez .mcp.json)
    bin/atlas check      hak SessionStart: podpowiedź dla Claude'a, gdy brakuje zależności (hooks/hooks.json)
    bin/atlas doctor     diagnostyka: Python, .NET, silnik (używa jej skill /data-architect:setup)
    bin/atlas build-engine  kompilacja silnika z kodu wtyczki (wymaga .NET SDK 8+; woła ją /data-architect:setup)
    bin/atlas ui         sama aplikacja webowa, bez Claude Code (do Ctrl+C)
    bin/atlas install    (obsługuje sam launcher) przygotowanie środowiska Pythona przez uv

Opcja --project wskazuje katalog projektu; domyślnie ATLAS_PROJECT_DIR albo bieżący katalog.

Moduły wtyczki importujemy wewnątrz poleceń: hak `check` uruchamia się przy każdym starcie sesji Claude Code,
więc nie ładujemy wtedy niepotrzebnie MCP, FastAPI ani SQLAlchemy.
"""

import json
import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from data_architect.core.settings import SKIP_DIRS, resolve_project_dir

app = typer.Typer(add_completion=False, help="Data Architect: analiza projektów SSDT dla Claude Code.")

ProjectOption = Annotated[Path | None, typer.Option("--project", help="Katalog projektu SSDT")]


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context, project: ProjectOption = None) -> None:
    """Bez polecenia uruchamia serwer MCP (tak robi Claude Code)."""
    ctx.obj = (project or resolve_project_dir()).resolve()
    if ctx.invoked_subcommand is None:
        mcp(ctx)


@app.command()
def mcp(ctx: typer.Context) -> None:
    """Serwer MCP po stdio (działa do zamknięcia sesji Claude Code)."""
    from data_architect.core.workspace import open_workspace
    from data_architect.mcp_server import server

    server.run(open_workspace(ctx.obj))


@app.command()
def check(ctx: typer.Context) -> None:
    """Hak SessionStart: w projekcie SSDT sprawdza silnik.

    Milczy, gdy wszystko działa; inaczej podpowiada Claude'owi /data-architect:setup.
    """
    from data_architect.project_analysis.engine import engine_status

    if not has_sql_project(ctx.obj):
        return

    engine = engine_status()
    if not engine["ok"]:
        print(
            "Data Architect: brakuje zależności silnika analizy SSDT. " + engine["error"] + "\n"
            "Jeśli użytkownik chce korzystać z Data Architect, zaproponuj /data-architect:setup "
            "(sprawdzi i po zgodzie użytkownika doinstaluje brakujące składniki)."
        )


@app.command()
def doctor() -> None:
    """Diagnostyka: wersja Pythona, .NET SDK i stan silnika. Kod wyjścia 1, gdy silnik nie działa."""
    from data_architect.project_analysis.engine import engine_status, find_dotnet_sdk, sdk_versions

    engine = engine_status()
    sdk = find_dotnet_sdk()
    report = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "sdk": {"dotnet": str(sdk), "versions": sdk_versions(sdk)} if sdk else None,
        "engine": engine,
    }

    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise typer.Exit(0 if engine["ok"] else 1)


@app.command("build-engine")
def build_engine() -> None:
    """Kompiluje silnik analizy z kodu wtyczki (wymaga .NET SDK 8+ i dostępu do NuGet)."""
    from data_architect.core.errors import AtlasError
    from data_architect.project_analysis.engine import build_engine as build

    sys.stdout.reconfigure(encoding="utf-8")
    try:
        dll = build()
    except AtlasError as e:
        print(f"Data Architect: {e}")
        raise typer.Exit(1) from e
    print(f"Silnik gotowy: {dll}")


@app.command()
def ui(ctx: typer.Context, no_browser: Annotated[bool, typer.Option("--no-browser")] = False) -> None:
    """Sama aplikacja webowa, bez Claude Code (działa do Ctrl+C)."""
    from data_architect.core.workspace import open_workspace
    from data_architect.web_app.runner import serve_forever

    serve_forever(open_workspace(ctx.obj), open_in_browser=not no_browser)


def has_sql_project(root: Path, max_depth: int = 4) -> bool:
    """Czy w katalogu (do kilku poziomów w głąb) jest projekt SSDT (.sqlproj)."""
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        if any(name.lower().endswith(".sqlproj") for name in filenames):
            return True

        too_deep = len(Path(dirpath).parts) - root_depth >= max_depth
        dirnames[:] = [] if too_deep else [d for d in dirnames if d not in SKIP_DIRS]

    return False
