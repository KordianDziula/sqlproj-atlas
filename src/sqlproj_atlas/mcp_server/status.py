"""Stan projektu dla Claude'a (atlas_status): pierwsze narzędzie wywoływane przez każdy skill."""

import contextlib

import git

from sqlproj_atlas.architecture.map_view import map_model
from sqlproj_atlas.core.database import current_snapshot, snapshot_info
from sqlproj_atlas.core.workspace import Workspace
from sqlproj_atlas.project_analysis.engine import engine_status
from sqlproj_atlas.project_analysis.pipeline import found_projects


def project_status(ws: Workspace) -> dict:
    """Czy była analiza, nowe commity od niej, wskazówki, stan silnika, pozycje do wyjaśnienia, adres UI."""
    snapshot = model = last = None
    if ws.has_data:  # bez bazy projekt nie był analizowany; nie zakładamy jej przy samym odczycie stanu
        with ws.db.session() as s:
            snapshot = current_snapshot(s)
            model = map_model(s, ws.files.overrides()) if snapshot else None
            last = snapshot_info(snapshot)

    # --- commity od ostatniej analizy ---
    new_commits = []
    if snapshot and snapshot.commit_sha and ws.git.is_repo:
        # commit analizy mógł zniknąć z historii (np. po rebase); wtedy nie pokazujemy nowych commitów
        with contextlib.suppress(git.GitCommandError):
            new_commits = ws.git.log(snapshot.commit_sha)

    # --- silnik i obiekty ---
    engine = engine_status()
    engine_state = (
        {"ok": True, "version": engine["version"]} if engine["ok"] else {"ok": False, "error": engine["error"]}
    )
    objects = model["objects"] if model else []

    return {
        "projectDir": str(ws.project_dir),
        "engine": engine_state,
        "sqlProjectsFound": [
            {"name": p.name, "file": p.file, "format": p.format, "files": len(p.files)} for p in found_projects(ws)
        ],
        "initialized": snapshot is not None,
        "guidelines": bool(ws.files.guidelines()),
        "lastAnalysis": last,
        "newCommitsSinceAnalysis": len(new_commits),
        "recentCommits": [c.label() for c in new_commits[:15]],
        "uncommittedChanges": ws.git.is_dirty() if ws.git.is_repo else None,
        "objects": len(objects),
        "objectsInAutoDomains": sum(1 for o in objects if o["domain"].startswith("auto|")),
        "objectsWithoutDescription": sum(1 for o in objects if not o["described"]),
        "openIssues": model["openIssues"] if model else 0,
        "ui": ws.ui_url,
    }
