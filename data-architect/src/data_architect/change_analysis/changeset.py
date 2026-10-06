"""Zestaw zmian (changeset): porównanie dwóch analiz „było → jest” plus lista commitów jako kontekst.

Zmiany obiektów liczymy wyłącznie ze stanu (compare.py). Commity od poprzedniej analizy zapisujemy obok
(`stats.commitList`: skrót, autor, data, opis) bez wiązania z obiektami: pomagają Claude'owi opisać „dlaczego”,
a w UI pokazują, co działo się w repozytorium.
"""

import logging
from dataclasses import asdict

from git import GitCommandError
from sqlalchemy.orm import Session

from data_architect.change_analysis.compare import ObjectChange, compare_snapshots
from data_architect.core.database import bump_revision
from data_architect.core.git_history import GitHistory
from data_architect.core.models import Change, Changeset, Semantic, Snapshot
from data_architect.core.project_files import ProjectFiles, now_iso

log = logging.getLogger(__name__)


def create_changeset(s: Session, git: GitHistory, files: ProjectFiles, from_id: int, to_id: int) -> int:
    """Zapisuje zestaw zmian między snapshotami i zwraca jego id."""
    before, after = s.get(Snapshot, from_id), s.get(Snapshot, to_id)
    changes = compare_snapshots(s, from_id, to_id)
    commits = _commits_between(git, before, after)

    changeset = Changeset(
        from_snapshot=from_id, to_snapshot=to_id, created_at=now_iso(), stats=_stats(changes, commits)
    )
    s.add(changeset)
    s.flush()  # nadaje changeset.id

    for change in changes:
        s.add(
            Change(
                changeset_id=changeset.id,
                object_id=change.object_id,
                change_type=change.change_type,
                details=change.details,
                risk=change.risk,
            )
        )

    _carry_over_renames(s, files, changes)
    bump_revision(s)
    return changeset.id


def _commits_between(git: GitHistory, before: Snapshot, after: Snapshot) -> list[dict]:
    """Commity od poprzedniej analizy do bieżącej (od najnowszego); pusta lista, gdy nie da się ich ustalić."""
    if not git.is_repo or not before.commit_sha or not after.commit_sha or before.commit_sha == after.commit_sha:
        return []

    try:
        return [asdict(c) for c in git.log(before.commit_sha, after.commit_sha)]
    except GitCommandError:
        # commitu poprzedniej analizy nie ma w repozytorium (np. analiza z innego klonu): zmiany obiektów i tak są
        log.warning("Commit poprzedniej analizy %s nie istnieje w historii git", before.commit_sha[:7])
        return []


def _stats(changes: list[ObjectChange], commits: list[dict]) -> dict:
    """Liczniki zestawu (według rodzaju zmiany i ryzyka) oraz lista commitów."""
    counts = {"added": 0, "modified": 0, "removed": 0, "renamed": 0, "high": 0, "med": 0}
    for change in changes:
        counts[change.change_type] += 1
        if change.risk in ("high", "med"):
            counts[change.risk] += 1

    counts["commits"] = len(commits)
    counts["authors"] = len({c["author"] for c in commits})
    return counts | {"commitList": commits}


# ---------------------------------------------------------------------------
# Zmiany nazw: semantyka i poprawki przechodzą na nową nazwę
# ---------------------------------------------------------------------------


def _carry_over_renames(s: Session, files: ProjectFiles, changes: list[ObjectChange]) -> None:
    renamed = [c for c in changes if c.change_type == "renamed"]
    if not renamed:
        return

    for change in renamed:
        old_id = change.details["oldId"]
        old = s.get(Semantic, old_id)
        if old:
            s.merge(
                Semantic(
                    object_id=change.object_id,
                    domain_id=old.domain_id,
                    description=old.description,
                    origin=old.origin,
                    updated_at=now_iso(),
                )
            )

    with files.edit_overrides() as ov:
        for change in renamed:
            old_id = change.details["oldId"]
            if old_id in ov.objects:
                ov.objects[change.object_id] = ov.objects.pop(old_id)
