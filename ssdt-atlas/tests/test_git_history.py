"""Historia git: lista commitów od poprzedniej analizy, HEAD i niezatwierdzone zmiany plików SSDT."""

from ssdt_atlas.core.git_history import GitHistory

from make_test_project import make


def test_log_since_previous_analysis(tmp_path):
    root = make(tmp_path / "sklep", "A")
    tag = GitHistory(root).head()
    make(root, "B")
    history = GitHistory(root)

    commits = history.log(tag)
    assert [c.message for c in commits][:2] == ["Index for stock lookups", "Multi-currency orders"]
    assert len(commits) == 7
    assert history.log(tag, tag) == []
    assert history.log(tag, history.head())[0].sha == history.head()


def test_project_in_subdirectory(tmp_path):
    # projekt w podkatalogu repozytorium: tylko commity dotykające plików projektu
    root = make(tmp_path / "sklep", "A")
    whole = GitHistory(root).log(None)
    sales = GitHistory(root / "src" / "Sales").log(None)
    dwh = GitHistory(root / "src" / "DWH").log(None)

    assert 0 < len(sales) < len(whole)
    assert 0 < len(dwh) < len(whole)
    assert sales[-1].message == "Initial import of Sales database"


def test_working_changes_make_repository_dirty(project_a):
    history = GitHistory(project_a)
    assert history.is_dirty() is False

    (project_a / "src" / "Sales" / "cat" / "Views" / "vw_New.sql").write_text(
        "CREATE VIEW [cat].[vw_New] AS SELECT 1 AS [X];"
    )
    assert history.is_dirty() is True


def test_modified_file_makes_repository_dirty(project_a):
    table = project_a / "src" / "Sales" / "dbo" / "Tables" / "Countries.sql"
    table.write_text(table.read_text(encoding="utf-8") + "\n-- uwaga\n", encoding="utf-8")
    assert GitHistory(project_a).is_dirty() is True


def test_commit_info_and_label(project_a):
    history = GitHistory(project_a)
    first = history.log(None)[-1]
    assert first.message == "Initial import of Sales database"
    assert first.label().startswith(first.sha[:7] + " 2024-03-11 Marek Zieliński: ")
    assert history.commit_info(first.sha) == first


# ---------------------------------------------------------------------------
# Przypadki brzegowe
# ---------------------------------------------------------------------------


def test_folder_outside_repository(tmp_path):
    history = GitHistory(tmp_path)
    assert history.is_repo is False
    assert history.head() is None
    assert history.is_dirty() is False


def test_repository_without_commits(tmp_path):
    import git

    git.Repo.init(tmp_path)
    (tmp_path / "a.sql").write_text("SELECT 1", encoding="utf-8")
    history = GitHistory(tmp_path)
    assert history.is_repo is True
    assert history.head() is None
    assert history.is_dirty() is False, "bez commita nie ma względem czego porównać"


def test_non_ssdt_files_are_ignored(project_a):
    (project_a / "notes.txt").write_text("x", encoding="utf-8")
    assert GitHistory(project_a).is_dirty() is False
