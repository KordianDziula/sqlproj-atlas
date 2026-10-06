"""Historia git projektu (GitPython, tylko odczyt).

Git służy wyłącznie jako kontekst: zmiany obiektów liczymy z porównania dwóch analiz (change_analysis/compare.py),
a z git bierzemy commit bieżącej analizy, listę commitów od poprzedniej (opisy dla Claude'a i UI)
i informację, czy są niezatwierdzone zmiany.

Analizowany projekt może leżeć w podkatalogu repozytorium, dlatego bierzemy pod uwagę tylko pliki tego katalogu.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import git

SSDT_FILE = re.compile(r"\.(sql|sqlproj|refactorlog)$", re.IGNORECASE)


@dataclass
class CommitInfo:
    sha: str
    author: str
    date: str  # ISO 8601 z ofsetem strefy, np. 2026-09-10T10:00:00+02:00
    message: str  # pierwsza linia opisu

    def label(self) -> str:
        """Krótki opis do list: „a1b2c3d 2026-09-10 Autor: opis”."""
        return f"{self.sha[:7]} {self.date[:10]} {self.author}: {self.message}"


class GitHistory:
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        try:
            self.repo = git.Repo(project_dir, search_parent_directories=True)
            # ścieżka projektu względem katalogu głównego repozytorium ("" gdy to ten sam katalog)
            root = Path(self.repo.working_tree_dir).resolve()
            prefix = Path(project_dir).resolve().relative_to(root).as_posix()
            self.prefix = "" if prefix == "." else prefix + "/"
        except (git.InvalidGitRepositoryError, git.NoSuchPathError, ValueError):
            self.repo = None
            self.prefix = ""

    @property
    def is_repo(self) -> bool:
        return self.repo is not None

    def head(self) -> str | None:
        """SHA bieżącego commita (None poza repozytorium albo w repozytorium bez commitów)."""
        if not self.repo:
            return None
        try:
            return self.repo.head.commit.hexsha
        except ValueError:
            return None

    def commit_info(self, sha: str) -> CommitInfo:
        return _info(self.repo.commit(sha))

    def log(self, since: str | None, until: str = "HEAD") -> list[CommitInfo]:
        """Commity od `since` (wyłącznie) do `until` dotykające projektu, od najnowszego.

        Rzuca git.GitCommandError, gdy `since` nie istnieje w historii (np. analiza z innego klonu).
        """
        revisions = f"{since}..{until}" if since else until
        return [_info(c) for c in self.repo.iter_commits(revisions, paths=self.prefix or ".")]

    def is_dirty(self) -> bool:
        """Czy są niezatwierdzone zmiany w plikach SSDT projektu (także nowe, nieśledzone pliki)."""
        if not self.repo or not self.head():
            return False

        changed = [d.a_path for d in self.repo.head.commit.diff(None)] + self.repo.untracked_files
        return any(path.startswith(self.prefix) and SSDT_FILE.search(path) for path in changed)


def _info(commit: git.Commit) -> CommitInfo:
    return CommitInfo(
        sha=commit.hexsha,
        author=commit.author.name,
        date=commit.authored_datetime.isoformat(),
        message=commit.summary if isinstance(commit.summary, str) else commit.summary.decode("utf-8", "replace"),
    )
