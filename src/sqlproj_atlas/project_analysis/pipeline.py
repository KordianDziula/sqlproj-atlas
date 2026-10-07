"""Analiza projektu: projekty .sqlproj → silnik DacFx → snapshot w bazie.

Operacja jest chroniona blokadą `ws.analysis_lock`, więc dwie analizy (np. z UI i od Claude'a) nie nałożą się.
Odświeżenie, czyli analiza plus porównanie z poprzednią, jest w change_analysis/refresh.py.
"""

from sqlproj_atlas.architecture.auto_domains import ensure_auto_domains
from sqlproj_atlas.core.project_files import now_iso
from sqlproj_atlas.core.workspace import Workspace
from sqlproj_atlas.project_analysis.engine import run_engine
from sqlproj_atlas.project_analysis.snapshot import SnapshotMeta, save_snapshot
from sqlproj_atlas.project_analysis.sqlproj import (
    SqlProject,
    discover_projects,
    list_files,
    parse_refactor_log,
    read_text,
)


def analyze(ws: Workspace, label: str | None = None) -> int:
    """Pełna analiza katalogu roboczego. Zwraca id nowego snapshotu."""
    with ws.analysis_lock:
        return analyze_unlocked(ws, label)


def analyze_unlocked(ws: Workspace, label: str | None = None) -> int:
    """Analiza bez blokady: dla wywołującego, który już trzyma `ws.analysis_lock` (odświeżenie)."""
    ws.ensure_data_dir()

    # 1. projekty do analizy (bez pominiętych w config.json) i zmiany nazw z .refactorlog
    files = list_files(ws.project_dir)
    excluded = ws.files.excluded_projects()
    found = discover_projects(ws.project_dir, files)
    projects = [p for p in found if p.name.lower() not in excluded]
    excluded_projects = [p.name for p in found if p.name.lower() in excluded]

    for p in projects:
        if p.refactorlog and p.refactorlog in files:
            p.renames = parse_refactor_log(read_text(ws.project_dir, p.refactorlog))

    # 2. silnik DacFx: treść plików → obiekty, relacje, problemy
    engine_input = [
        {"name": p.name, "dsp": p.dsp, "files": [{"path": f, "content": read_text(ws.project_dir, f)} for f in p.files]}
        for p in projects
    ]
    engine_projects = run_engine(engine_input, ws.work_dir).projects if engine_input else []

    # 3. zapis snapshotu (bez schematów pominiętych w config.json); nowe obiekty trafiają do domen automatycznych
    with ws.db.session() as s:
        meta = _current_commit(ws, label)
        snapshot_id = save_snapshot(s, meta, projects, engine_projects, ws.files.excluded_schemas(), excluded_projects)
        ensure_auto_domains(s, snapshot_id)
        return snapshot_id


def found_projects(ws: Workspace) -> list[SqlProject]:
    """Projekty SSDT w katalogu roboczym (bez filtrowania przez config.json)."""
    return discover_projects(ws.project_dir)


def _current_commit(ws: Workspace, label: str | None) -> SnapshotMeta:
    """Metadane snapshotu: commit HEAD (jeśli projekt jest w repozytorium) i czy są niezatwierdzone zmiany."""
    head = ws.git.head()
    if not head:
        return SnapshotMeta(commit_sha=None, commit_date=now_iso(), author=None, message=None, label=label)

    info = ws.git.commit_info(head)
    return SnapshotMeta(
        commit_sha=head,
        commit_date=info.date,
        author=info.author,
        message=info.message,
        dirty=ws.git.is_dirty(),
        label=label,
    )
