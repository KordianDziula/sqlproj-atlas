"""Zestaw zmian i odświeżenie (change_analysis/changeset.py, refresh.py) na prawdziwym repozytorium i silniku."""

import pytest

from data_architect.change_analysis.refresh import refresh
from data_architect.core.models import Change, Changeset, Semantic, Snapshot
from data_architect.project_analysis.pipeline import analyze

from conftest import requires_engine
from make_test_project import make

pytestmark = requires_engine


def latest_changeset(ws) -> tuple[Changeset, dict[str, Change]]:
    with ws.db.session() as s:
        changeset = s.query(Changeset).order_by(Changeset.id.desc()).first()
        changes = {c.object_id: c for c in s.query(Change).filter_by(changeset_id=changeset.id)}
        return changeset, changes


def test_first_refresh_without_previous_analysis_only_analyzes(workspace):
    result = refresh(workspace)
    assert result["previous"] is None
    assert result["changeset"] is None
    assert result["snapshot"] > 0


def test_refresh_without_changes(analyzed):
    result = refresh(analyzed)
    changeset, changes = latest_changeset(analyzed)
    assert result["changeset"] == changeset.id
    assert changes == {}
    assert changeset.stats["commits"] == 0
    assert changeset.stats["commitList"] == []


def test_commit_list_statistics(refreshed):
    changeset, _ = latest_changeset(refreshed)
    stats = changeset.stats
    assert stats["commits"] == 7
    assert stats["authors"] == 4  # pk, aw, kl, jn
    assert (stats["added"], stats["removed"], stats["renamed"]) == (2, 1, 1)
    assert stats["high"] >= 1


def test_commit_list_is_context_only(refreshed):
    # commity są listą od najnowszego, bez wiązania z obiektami
    changeset, changes = latest_changeset(refreshed)
    discounts = next(c for c in changeset.stats["commitList"] if c["message"] == "Remove legacy discounts")
    assert discounts["author"] == "Jan Nowak"
    assert discounts["date"].startswith("2026-09-28")
    assert changes["Sales|sales.orders"].change_type == "modified"


def test_rename_carries_over_semantics_and_user_overrides(analyzed):
    with analyzed.db.session() as s:
        s.merge(
            Semantic(
                object_id="Sales|pay.transactions",
                domain_id="auto|Sales|pay",
                description="Transakcje",
                origin="claude",
            )
        )
    analyzed.files.set_override("objects", "Sales|pay.transactions", "description", "Opis użytkownika")

    make(analyzed.project_dir, "B")
    refresh(analyzed)

    with analyzed.db.session() as s:
        assert s.get(Semantic, "Sales|pay.paymenttransactions").description == "Transakcje"
    objects = analyzed.files.overrides().objects
    assert objects["Sales|pay.paymenttransactions"].description == "Opis użytkownika"
    assert "Sales|pay.transactions" not in objects


def test_deleted_file_in_working_tree(analyzed):
    (analyzed.project_dir / "src" / "Sales" / "dbo" / "Tables" / "Countries.sql").unlink()
    refresh(analyzed)

    changeset, changes = latest_changeset(analyzed)
    assert changes["Sales|dbo.countries"].change_type == "removed"
    assert changeset.stats["commitList"] == [], "niezatwierdzona zmiana: nowy stan bez commitów"


def test_amended_commit_lists_new_history(analyzed):
    # po „git commit --amend” stary commit nadal istnieje w repozytorium, więc lista pokazuje przepisany commit
    repo = analyzed.git.repo
    repo.git.commit("--amend", "-m", "Catalog view (amended)")
    refresh(analyzed)

    changeset, _ = latest_changeset(analyzed)
    assert [c["message"] for c in changeset.stats["commitList"]] == ["Catalog view (amended)"]


def test_missing_commit_of_previous_analysis_keeps_object_changes(analyzed):
    # commitu poprzedniej analizy nie ma w repozytorium (np. analiza z innego klonu albo po wyczyszczeniu historii)
    with analyzed.db.session() as s:
        s.query(Snapshot).update({Snapshot.commit_sha: "0" * 40})
    (analyzed.project_dir / "src" / "Sales" / "dbo" / "Tables" / "Countries.sql").unlink()
    repo = analyzed.git.repo
    repo.git.add(A=True)
    repo.index.commit("Remove countries")

    refresh(analyzed)

    changeset, changes = latest_changeset(analyzed)
    assert changes["Sales|dbo.countries"].change_type == "removed"
    assert changeset.stats["commitList"] == []


def test_refresh_without_git_repository(tmp_path, analyzed_template):
    # projekt skopiowany bez katalogu .git: analiza i porównanie działają, lista commitów jest pusta
    import shutil

    from data_architect.core.workspace import Workspace

    root = tmp_path / "bez-gita"
    shutil.copytree(analyzed_template, root, ignore=shutil.ignore_patterns(".git"))
    ws = Workspace(root)
    try:
        assert ws.git.is_repo is False
        (root / "src" / "Sales" / "dbo" / "Tables" / "Countries.sql").unlink()
        refresh(ws)
        changeset, changes = latest_changeset(ws)
        assert changes["Sales|dbo.countries"].change_type == "removed"
        assert changeset.stats["commitList"] == []
    finally:
        ws.close()


def test_concurrent_refreshes_are_serialized(analyzed):
    import threading

    results, errors = [], []

    def run():
        try:
            results.append(refresh(analyzed))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len({r["snapshot"] for r in results}) == 2, "dwie osobne analizy, jedna po drugiej"


@pytest.mark.parametrize("label", ["etykieta"])
def test_analyze_stores_label(analyzed, label):
    sid = analyze(analyzed, label)
    with analyzed.db.session() as s:
        assert s.get(Snapshot, sid).label == label
