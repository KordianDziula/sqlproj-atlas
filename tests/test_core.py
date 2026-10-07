"""Rdzeń (core/): pliki w repozytorium projektu, baza, ustawienia, kontekst projektu."""

import json
from pathlib import Path

import pytest

from sqlproj_atlas.core import settings
from sqlproj_atlas.core.database import bump_revision, current_snapshot, revision, snapshot_info
from sqlproj_atlas.core.project_files import Overrides, ProjectFiles, now_iso
from sqlproj_atlas.core.workspace import Workspace, open_workspace

from builders import add_snapshot, table

# ---------------------------------------------------------------------------
# overrides.json
# ---------------------------------------------------------------------------


def test_overrides_round_trip(tmp_path):
    files = ProjectFiles(tmp_path)

    files.set_override("objects", "Sales|sales.orders", "description", "Opis")
    files.set_override("objects", "Sales|sales.orders", "domain", "Sales|klienci")
    with files.edit_overrides() as ov:
        ov.add_edge("Sales|a", "Sales|b", "reads", "test")
        ov.add_edge("Sales|a", "Sales|b", "reads", "duplikat")

    ov = files.overrides()
    assert ov.objects["Sales|sales.orders"].description == "Opis"
    assert ov.objects["Sales|sales.orders"].updatedAt.endswith("Z")
    assert [e.note for e in ov.edges] == ["test"], "ta sama relacja tylko raz"
    assert '"from": "Sales|a"' in files.overrides_file.read_text(encoding="utf-8"), "w pliku pole nazywa się „from”"

    # pusta wartość usuwa pole, a wpis bez pól znika
    files.set_override("objects", "Sales|sales.orders", "description", "")
    files.set_override("objects", "Sales|sales.orders", "domain", None)
    assert "Sales|sales.orders" not in files.overrides().objects


def test_overrides_file_is_sorted_and_deterministic(tmp_path):
    files = ProjectFiles(tmp_path)
    files.set_override("notes", "ext|zeta", "description", "z")
    files.set_override("notes", "ext|alfa", "description", "a")

    data = json.loads(files.overrides_file.read_text(encoding="utf-8"))
    assert list(data["notes"]) == ["ext|alfa", "ext|zeta"]
    assert files.overrides_file.read_text(encoding="utf-8").endswith("}\n")


def test_unknown_fields_in_overrides_are_kept(tmp_path):
    files = ProjectFiles(tmp_path)
    files.overrides_file.write_text(
        json.dumps({"objects": {"A|a.b": {"description": "x", "przyszłePole": 1}}, "inne": True}), encoding="utf-8"
    )
    with files.edit_overrides():
        pass
    data = json.loads(files.overrides_file.read_text(encoding="utf-8"))
    assert data["objects"]["A|a.b"]["przyszłePole"] == 1
    assert data["inne"] is True


def test_corrupted_overrides_are_treated_as_empty(tmp_path):
    files = ProjectFiles(tmp_path)
    files.overrides_file.write_text("{ to nie jest json", encoding="utf-8")
    assert files.overrides() == Overrides()


def test_overrides_changed_by_another_process_are_visible(tmp_path):
    files = ProjectFiles(tmp_path)
    other = ProjectFiles(tmp_path)
    other.set_override("objects", "A|a.b", "description", "z innej sesji")
    assert files.overrides().objects["A|a.b"].description == "z innej sesji"


# ---------------------------------------------------------------------------
# guidelines.md, config.json, .gitignore
# ---------------------------------------------------------------------------


def test_guidelines(tmp_path):
    files = ProjectFiles(tmp_path)
    assert files.guidelines() is None
    files.save_guidelines("## A")
    assert files.guidelines() == "## A\n"
    files.save_guidelines("## B\n")
    assert files.guidelines() == "## B\n"


def test_config(tmp_path):
    files = ProjectFiles(tmp_path)
    assert files.config() == {"excludeProjects": [], "excludeSchemas": []}
    files.save_config({"excludeProjects": ["Tests", "Sample"], "excludeSchemas": ["tmp", "Sales.Audit", " "]})
    assert files.excluded_projects() == {"tests", "sample"}
    assert files.excluded_schemas() == {"tmp", "sales.audit"}

    files.save_config({"excludeProjects": ["Tests"]})
    assert files.excluded_schemas() == set(), "konfiguracja sprzed 1.5.0 bez excludeSchemas"

    files.config_file.write_text("zepsuty", encoding="utf-8")
    assert files.config() == {"excludeProjects": [], "excludeSchemas": []}


def test_data_folder_is_created_on_first_write(tmp_path):
    data_dir = tmp_path / "dane"
    files = ProjectFiles(data_dir)
    assert files.overrides().objects == {} and files.guidelines() is None
    assert files.config() == {"excludeProjects": [], "excludeSchemas": []}
    assert not data_dir.exists(), "odczyt niczego nie tworzy"

    files.save_guidelines("# Wskazówki")
    assert "atlas.db*" in (data_dir / ".gitignore").read_text(encoding="utf-8")


def test_gitignore_is_created_once(tmp_path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("# moje\n", encoding="utf-8")
    ProjectFiles(tmp_path).save_config({"excludeProjects": []})
    assert gitignore.read_text(encoding="utf-8") == "# moje\n", "istniejący plik nie jest nadpisywany"


def test_now_iso_format():
    stamp = now_iso()
    assert len(stamp) == 24 and stamp.endswith("Z") and stamp[10] == "T"


# ---------------------------------------------------------------------------
# Baza
# ---------------------------------------------------------------------------


def test_revision_counter(db):
    with db.session() as s:
        assert revision(s) == 0
        assert bump_revision(s) == 1
        assert bump_revision(s) == 2
    with db.session() as s:
        assert revision(s) == 2


def test_current_snapshot_and_info(db):
    with db.session() as s:
        assert current_snapshot(s) is None
        assert snapshot_info(None) is None
        add_snapshot(s, [table("A|a.b")], commit_sha="abcdef1234567")
        sid = add_snapshot(s, [table("A|a.b")], commit_sha=None, dirty=True)

        latest = current_snapshot(s)
        assert latest.id == sid
        info = snapshot_info(latest)
        assert (info["sha"], info["shortSha"], info["dirty"]) == (None, None, True)


def test_transaction_is_rolled_back_on_error(db):
    with pytest.raises(RuntimeError), db.session() as s:
        bump_revision(s)
        raise RuntimeError("błąd w trakcie zapisu")
    with db.session() as s:
        assert revision(s) == 0


# ---------------------------------------------------------------------------
# Ustawienia i kontekst projektu
# ---------------------------------------------------------------------------


def test_project_dir_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_PROJECT_DIR", str(tmp_path))
    assert settings.resolve_project_dir() == tmp_path.resolve()


@pytest.mark.parametrize("value", ["${CLAUDE_PROJECT_DIR}", "C:/nie/istnieje/wcale"])
def test_invalid_project_dir_falls_back_to_current_dir(monkeypatch, tmp_path, value):
    monkeypatch.setenv("ATLAS_PROJECT_DIR", value)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert settings.resolve_project_dir() == tmp_path


def test_type_letters():
    assert settings.type_letter("Table") == "T"
    assert settings.type_letter("TableValuedFunction") == "F"
    assert settings.type_letter("Cokolwiek") == "?"


def test_plugin_paths_exist():
    assert (settings.UI_DIR / "index.html").exists()
    assert (settings.PLUGIN_ROOT / ".claude-plugin" / "plugin.json").exists()


def test_workspace_creates_data_folder_lazily(tmp_path):
    ws = Workspace(tmp_path)
    try:
        assert ws.git.is_repo is False
        assert ws.ui_url is None
        assert ws.has_data is False
        assert not (tmp_path / ".claude").exists(), "sam Workspace niczego nie tworzy"

        assert ws.db is not None  # pierwsze użycie bazy zakłada katalog z .gitignore
        assert ws.has_data is True
        assert (ws.data_dir / ".gitignore").exists()
    finally:
        ws.close()


def test_override_creates_data_folder(tmp_path):
    ws = Workspace(tmp_path)
    try:
        ws.files.set_override("objects", "Sales|dbo.t", "description", "opis")
        assert (ws.data_dir / "overrides.json").exists()
        assert (ws.data_dir / ".gitignore").exists()
        assert ws.has_data is False, "poprawka nie zakłada bazy"
    finally:
        ws.close()


def test_legacy_data_folder_is_moved(tmp_path):
    legacy = tmp_path / ".claude" / "data-architect"
    legacy.mkdir(parents=True)
    (legacy / "guidelines.md").write_text("## Stare wskazówki\n", encoding="utf-8")

    ws = Workspace(tmp_path)
    try:
        assert not legacy.exists()
        assert ws.files.guidelines() == "## Stare wskazówki\n"
    finally:
        ws.close()


def test_newest_legacy_data_folder_wins(tmp_path):
    for name, text in (("data-architect", "## Najstarsze\n"), ("ssdt-atlas", "## Z wersji 1.6\n")):
        (tmp_path / ".claude" / name).mkdir(parents=True)
        (tmp_path / ".claude" / name / "guidelines.md").write_text(text, encoding="utf-8")

    ws = Workspace(tmp_path)
    try:
        assert ws.files.guidelines() == "## Z wersji 1.6\n"
        assert (tmp_path / ".claude" / "data-architect").exists(), "starszego katalogu nie ruszamy"
    finally:
        ws.close()


def test_legacy_data_folder_is_left_when_new_one_exists(tmp_path):
    legacy = tmp_path / ".claude" / "data-architect"
    legacy.mkdir(parents=True)
    (tmp_path / ".claude" / "sqlproj-atlas").mkdir()
    Workspace(tmp_path).close()
    assert legacy.exists(), "nie nadpisujemy nowszych danych"


def test_legacy_data_folder_that_cannot_be_moved(tmp_path, monkeypatch, caplog):
    (tmp_path / ".claude" / "data-architect").mkdir(parents=True)

    def locked(self, target):
        raise PermissionError("plik w użyciu")

    monkeypatch.setattr(Path, "rename", locked)
    ws = Workspace(tmp_path)
    ws.close()
    assert "Nie udało się przenieść" in caplog.text
    assert ws.has_data is False


def test_open_workspace_without_data_folder_creates_nothing(tmp_path):
    ws = open_workspace(tmp_path / "inny-projekt")
    try:
        (tmp_path / "inny-projekt").mkdir()
        assert list((tmp_path / "inny-projekt").iterdir()) == []
    finally:
        ws.close()


def test_open_workspace_is_shared_and_logs_to_file(tmp_path):
    (tmp_path / ".claude" / "sqlproj-atlas").mkdir(parents=True)  # projekt po analizie: log od razu do pliku
    ws = open_workspace(tmp_path)
    try:
        assert open_workspace(tmp_path) is ws
        import logging

        logging.getLogger("sqlproj_atlas.test").info("wpis testowy")
        assert "wpis testowy" in (ws.data_dir / "atlas.log").read_text(encoding="utf-8")
    finally:
        ws.close()
        settings.setup_logging()  # zamyka plik logu, żeby katalog testu dało się usunąć
