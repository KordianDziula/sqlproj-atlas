"""Silnik analizy atlas-engine (C#, Microsoft DacFx + ScriptDom), uruchamiany jako proces .NET.

Przepływ:
    1. zapisujemy pliki projektów do JSON,
    2. `dotnet atlas-engine.dll analyze in.json --out out.json`,
    3. czytamy wynik do modeli Pydantic (EngineResult).

Kod silnika: katalog engine/ (Program.cs, ProjectAnalyzer.cs, ScriptAnalyzer.cs). Wtyczka nie zawiera skompilowanych
plików: /data-architect:setup buduje silnik na komputerze użytkownika (`bin/atlas build-engine`, wymaga .NET SDK 8+)
do ~/.data-architect/engine/<skrót kodu>. Nowa wersja kodu silnika trafia do nowego katalogu, więc starsze wersje
wtyczki w innych sesjach dalej działają.
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from data_architect.core.errors import AtlasError
from data_architect.core.settings import ENGINE_SOURCE, engine_home

log = logging.getLogger(__name__)

# procesy potomne bez migającego okna konsoli na Windows
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ANALYSIS_TIMEOUT = 30 * 60  # sekundy; duże solucje analizują się kilka minut
BUILD_TIMEOUT = 15 * 60  # pierwsza kompilacja pobiera pakiety z NuGet
MIN_DOTNET = 8

# zmienne dla dotnet: bez telemetrii i komunikatów powitalnych
_DOTNET_ENV = {"DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1"}

NO_SDK = (
    "Nie znaleziono .NET SDK 8 lub nowszego, potrzebnego do zbudowania silnika analizy. "
    "Zainstaluj przez /data-architect:setup albo z https://dotnet.microsoft.com/download"
)


# ---------------------------------------------------------------------------
# Wynik silnika (JSON w camelCase → pola snake_case)
# ---------------------------------------------------------------------------


class _EngineModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class EngineColumn(_EngineModel):
    name: str
    type: str = ""
    nullable: bool = False
    identity: bool = False
    pk: bool = False
    fk: bool = False
    computed: bool = False


class EngineParam(_EngineModel):
    name: str
    type: str = ""
    output: bool = False


class EngineObject(_EngineModel):
    key: str  # „schemat.nazwa” małymi literami
    schema_name: str = Field(alias="schema")
    name: str
    type: str  # Table, View, Procedure, ...
    file: str | None = None
    line: int = 0
    hash: str
    definition: str | None = None
    columns: list[EngineColumn] | None = None
    params: list[EngineParam] | None = None


class EngineEdge(_EngineModel):
    source: str = Field(alias="from")
    to: str
    kind: str  # reads, writes, calls, fk, on, ...
    external_db: str | None = None  # odwołanie do innej bazy: [$(Staging)] albo [ErpDb]
    external_server: str | None = None  # linked server


class EngineIssue(_EngineModel):
    object: str
    kind: str  # dynamic, unresolved, openquery
    ref: str = ""
    db: str | None = None
    server: str | None = None
    line: int = 0
    snippet: str | None = None
    message: str | None = None


class EngineParseError(_EngineModel):
    file: str
    line: int = 0
    message: str = ""


class EngineProject(_EngineModel):
    name: str
    platform: str = ""
    objects: list[EngineObject] = []
    edges: list[EngineEdge] = []
    issues: list[EngineIssue] = []
    parse_errors: list[EngineParseError] = []
    messages: list[str] = []


class EngineResult(_EngineModel):
    engine: str = ""
    projects: list[EngineProject] = []


# ---------------------------------------------------------------------------
# Wyszukiwanie .NET
# ---------------------------------------------------------------------------


def dotnet_candidates() -> list[Path]:
    """Miejsca, w których szukamy dotnet.

    Nie polegamy tylko na PATH i zmiennych środowiskowych: Claude Code uruchamia serwery MCP z okrojonym
    środowiskiem (np. bez LOCALAPPDATA), więc ścieżki wyznaczamy też z katalogu domowego.
    /data-architect:setup instaluje .NET do ~/.dotnet.
    """
    windows = sys.platform == "win32"
    exe = "dotnet.exe" if windows else "dotnet"
    home = Path.home()
    env = os.environ.get

    candidates = [env("ATLAS_DOTNET"), env("DOTNET_ROOT") and Path(env("DOTNET_ROOT")) / exe]
    if windows:
        candidates += [
            env("LOCALAPPDATA") and Path(env("LOCALAPPDATA")) / "Microsoft" / "dotnet" / exe,
            home / "AppData" / "Local" / "Microsoft" / "dotnet" / exe,
            Path(env("ProgramFiles") or r"C:\Program Files") / "dotnet" / exe,
            Path(r"C:\Program Files\dotnet\dotnet.exe"),
        ]
    candidates.append(home / ".dotnet" / exe)
    if not windows:
        # instalator Microsoftu (macOS), pakiety Linuksa, Homebrew (Apple Silicon i Intel)
        system_paths = (
            "/usr/local/share/dotnet/dotnet", "/usr/share/dotnet/dotnet", "/usr/lib/dotnet/dotnet",
            "/opt/homebrew/bin/dotnet", "/usr/local/bin/dotnet",
        )  # fmt: skip
        candidates += [Path(p) for p in system_paths]

    return [Path(c) for c in candidates if c]


def find_dotnet() -> Path | None:
    """Pierwszy istniejący dotnet z listy kandydatów albo z PATH.

    Szukamy za każdym razem, bo .NET mógł zostać doinstalowany w trakcie sesji.
    """
    for candidate in dotnet_candidates():
        if candidate.is_file():
            return candidate

    on_path = shutil.which("dotnet")
    return Path(on_path) if on_path else None


def sdk_versions(dotnet: Path) -> list[str]:
    """Wersje .NET SDK 8+ zainstalowane obok danego dotnet (katalog sdk/), bez uruchamiania procesu.

    dotnet z Homebrew albo z /usr/local/bin to dowiązanie, więc szukamy obok pliku docelowego.
    """
    sdk_dir = dotnet.resolve().parent / "sdk"
    if not sdk_dir.is_dir():
        return []
    return sorted(d.name for d in sdk_dir.iterdir() if d.is_dir() and _major(d.name) >= MIN_DOTNET)


def find_dotnet_sdk() -> Path | None:
    """Pierwszy dotnet z .NET SDK 8+. Może to być inna instalacja niż ta do uruchamiania (np. sam runtime
    w Program Files, a SDK w katalogu domowym)."""
    on_path = shutil.which("dotnet")
    for candidate in [*dotnet_candidates(), *([Path(on_path)] if on_path else [])]:
        if candidate.is_file() and sdk_versions(candidate):
            return candidate
    return None


def _major(version: str) -> int:
    head = version.split(".")[0]
    return int(head) if head.isdigit() else 0


# ---------------------------------------------------------------------------
# Budowa silnika z kodu w engine/
# ---------------------------------------------------------------------------


def _source_files() -> list[Path]:
    return sorted([ENGINE_SOURCE / "AtlasEngine.csproj", *ENGINE_SOURCE.glob("*.cs")])


def engine_source_hash() -> str:
    """Skrót kodu silnika (csproj i pliki .cs). Końce linii nie mają znaczenia: Windows i macOS pobierają
    z git ten sam kod z innymi końcami linii, a powinny dostać ten sam katalog silnika."""
    digest = hashlib.sha256()
    for path in _source_files():
        digest.update(path.name.encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:12]


def engine_dll() -> Path:
    """Skompilowany silnik dla bieżącego kodu w engine/ (może jeszcze nie istnieć)."""
    return engine_home() / engine_source_hash() / "atlas-engine.dll"


def build_engine() -> Path:
    """Kompiluje silnik i zwraca ścieżkę DLL; gdy silnik dla tego kodu już istnieje, nic nie robi.

    Wymaga .NET SDK 8+ i dostępu do NuGet (pakiet Microsoft.SqlServer.DacFx). Kod kopiujemy do katalogu
    tymczasowego obok docelowego: katalog wtyczki zostaje nietknięty (bez bin/ i obj/), a przerwana
    kompilacja nie zostawia połowicznego silnika.
    """
    dll = engine_dll()
    if dll.exists():
        return dll

    dotnet = find_dotnet_sdk()
    if not dotnet:
        raise AtlasError(NO_SDK)

    target = dll.parent
    staging = target.with_name(f"{target.name}.build-{os.getpid()}")
    shutil.rmtree(staging, ignore_errors=True)
    (staging / "src").mkdir(parents=True)
    for path in _source_files():
        shutil.copy2(path, staging / "src" / path.name)

    try:
        out = staging / "out"
        log.info("Kompilacja silnika (%s) do %s", dotnet, target)
        _publish(dotnet, staging / "src" / "AtlasEngine.csproj", out)

        # symbole debugowania nie są potrzebne
        for pdb in out.glob("*.pdb"):
            pdb.unlink()

        try:
            out.rename(target)
        except OSError:
            # równoległa kompilacja w innej sesji zdążyła pierwsza: jej wynik jest równie dobry
            if not dll.exists():
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return dll


def _publish(dotnet: Path, project: Path, out: Path) -> None:
    """`dotnet publish`; błąd zamienia na AtlasError z końcówką wyjścia kompilatora (błędy idą na stdout)."""
    command = [
        str(dotnet), "publish", str(project), "-c", "Release", "-o", str(out),
        "-p:UseAppHost=false", "--nologo", "--disable-build-servers",
    ]  # fmt: skip
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=BUILD_TIMEOUT,
            stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW, env=os.environ | _DOTNET_ENV,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AtlasError(f"Kompilacja silnika nie powiodła się: {e}") from e

    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-15:])
        raise AtlasError(f"Kompilacja silnika nie powiodła się (dotnet publish, kod {result.returncode}):\n{tail}")


# ---------------------------------------------------------------------------
# Stan i uruchomienie silnika
# ---------------------------------------------------------------------------


def engine_status() -> dict:
    """Czy silnik da się uruchomić: {ok, dll, dotnet, version, error}.

    Używane przez atlas_status, doctor i hak startowy sesji.
    """
    dotnet = find_dotnet()
    dll = engine_dll()
    status = {
        "ok": False,
        "dll": str(dll),
        "dotnet": str(dotnet) if dotnet else None,
        "version": None,
        "error": None,
    }

    if not dotnet:
        checked = "; ".join(str(c) for c in dotnet_candidates())
        status["error"] = (
            "Nie znaleziono .NET (wymagany .NET SDK 8 lub nowszy do zbudowania silnika). Zainstaluj przez "
            f"/data-architect:setup albo z https://dotnet.microsoft.com/download. Sprawdzone: {checked} oraz PATH"
        )
        return status

    if not dll.exists():
        status["error"] = (
            "Silnik analizy nie jest jeszcze zbudowany na tym komputerze. Uruchom /data-architect:setup "
            "(zbuduje go z kodu wtyczki poleceniem bin/atlas build-engine; wymaga .NET SDK 8+)."
        )
        return status

    try:
        result = _run([str(dotnet), str(dll), "version"], timeout=60)
        status["version"] = json.loads(result.stdout)
        status["ok"] = True
    except (AtlasError, ValueError) as e:
        status["error"] = f"Silnik nie uruchomił się: {e}"

    return status


def run_engine(projects: list[dict], work_dir: Path) -> EngineResult:
    """Analizuje projekty: [{name, dsp, files: [{path, content}]}]. Pliki wymiany są usuwane po zakończeniu."""
    dotnet = find_dotnet()
    dll = engine_dll()
    if not dotnet or not dll.exists():
        raise AtlasError(engine_status()["error"])

    work_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(time.time() * 1000)}-{os.getpid()}"
    input_file = work_dir / f"engine-in-{stamp}.json"
    output_file = work_dir / f"engine-out-{stamp}.json"

    try:
        input_file.write_text(json.dumps({"projects": projects}, ensure_ascii=False), encoding="utf-8")

        started = time.monotonic()
        _run(
            [str(dotnet), str(dll), "analyze", str(input_file), "--out", str(output_file)],
            timeout=ANALYSIS_TIMEOUT,
        )
        result = EngineResult.model_validate_json(output_file.read_text(encoding="utf-8-sig"))

        file_count = sum(len(p["files"]) for p in projects)
        log.info("Silnik: %d projekt(ów), %d plików, %.1f s", len(projects), file_count, time.monotonic() - started)
        return result

    finally:
        input_file.unlink(missing_ok=True)
        output_file.unlink(missing_ok=True)


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Uruchamia silnik; błąd uruchomienia albo niezerowy kod wyjścia zamienia na AtlasError z treścią stderr."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
            stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AtlasError(f"atlas-engine: {e}") from e

    if result.returncode != 0:
        raise AtlasError(f"atlas-engine: {result.stderr.strip() or f'kod wyjścia {result.returncode}'}")
    return result
