"""Kontekst analizowanego projektu: baza, pliki w repozytorium, historia git.

Jeden obiekt Workspace na proces (serwer MCP albo samodzielne UI). Przekazujemy go do warstw
analizy, widoków, narzędzi MCP i API, żeby nie trzymać stanu w zmiennych globalnych modułów.

Katalog danych `.claude/sqlproj-atlas` powstaje dopiero przy pierwszym zapisie (analiza, wskazówki,
konfiguracja, poprawka, uruchomienie UI). Claude Code startuje serwer MCP w każdej sesji, także w projektach
niezwiązanych z SSDT, więc sam start niczego w projekcie nie tworzy.

Katalog sprzed zmiany nazwy wtyczki (`.claude/ssdt-atlas` albo `.claude/data-architect`) przenosimy na nową nazwę
po pierwszym otwarciu projektu.
"""

import logging
import threading
from pathlib import Path

from sqlproj_atlas.core.database import Database
from sqlproj_atlas.core.git_history import GitHistory
from sqlproj_atlas.core.project_files import ProjectFiles, prepare_data_dir
from sqlproj_atlas.core.settings import DATA_DIR_NAME, LEGACY_DATA_DIR_NAMES, setup_logging

log = logging.getLogger(__name__)


class Workspace:
    def __init__(self, project_dir: Path, log_to_file: bool = False):
        self.project_dir = project_dir
        self.data_dir = project_dir / DATA_DIR_NAME
        _move_legacy_data_dir(project_dir, self.data_dir)
        self.work_dir = self.data_dir / "work"  # pliki wymiany z silnikiem

        self.files = ProjectFiles(self.data_dir, prepare_dir=self.ensure_data_dir)
        self.git = GitHistory(project_dir)

        # jedna analiza naraz (np. dwa odświeżenia wywołane przez Claude'a)
        self.analysis_lock = threading.Lock()

        # stan lokalnej aplikacji webowej uruchomionej przez ten proces (web_app/runner.py)
        self.ui_url: str | None = None
        self.ui_server = None  # uvicorn.Server, gdy aplikację uruchomił ten proces (do zamknięcia)
        self.ui_thread: threading.Thread | None = None

        self._log_to_file = log_to_file
        self._prepared = False
        self._db: Database | None = None
        self._db_lock = threading.Lock()

    @property
    def has_data(self) -> bool:
        """Czy projekt ma już bazę wtyczki (była analiza albo zapis). Pozwala odczytać stan bez tworzenia plików."""
        return (self.data_dir / "atlas.db").exists()

    @property
    def db(self) -> Database:
        """Baza projektu, otwierana (i w razie potrzeby zakładana) przy pierwszym użyciu."""
        with self._db_lock:
            if self._db is None:
                self.ensure_data_dir()
                self._db = Database(self.data_dir / "atlas.db")
            return self._db

    def ensure_data_dir(self) -> None:
        """Zakłada katalog danych (z .gitignore) i włącza log do atlas.log w procesie serwera."""
        if self._prepared:
            return
        prepare_data_dir(self.data_dir)
        if self._log_to_file:
            setup_logging(self.data_dir / "atlas.log")
        self._prepared = True

    def close(self) -> None:
        """Zamyka połączenia z bazą (jeśli była otwarta)."""
        if self._db is not None:
            self._db.dispose()


def _move_legacy_data_dir(project_dir: Path, data_dir: Path) -> None:
    """Przenosi dane z katalogu starszej wersji (`.claude/ssdt-atlas` z 1.5–1.6 albo `.claude/data-architect`
    sprzed 1.5), jeśli nowego katalogu jeszcze nie ma."""
    legacy = next((project_dir / name for name in LEGACY_DATA_DIR_NAMES if (project_dir / name).is_dir()), None)
    if data_dir.exists() or legacy is None:
        return
    try:
        legacy.rename(data_dir)
        log.info("Przeniesiono dane wtyczki z %s do %s", legacy, data_dir)
    except OSError as e:
        # np. baza otwarta przez sesję ze starą wersją wtyczki; analiza zacznie się wtedy od nowa
        log.warning("Nie udało się przenieść %s do %s: %s", legacy, data_dir, e)


_current: Workspace | None = None


def open_workspace(project_dir: Path) -> Workspace:
    """Workspace procesu (tworzony przy pierwszym wywołaniu). Log do pliku włącza się, gdy katalog danych istnieje
    albo powstanie przy pierwszym zapisie; do tego czasu log idzie tylko na stderr."""
    global _current
    if _current is None or _current.project_dir != project_dir:
        _current = Workspace(project_dir, log_to_file=True)
        setup_logging()
        if _current.data_dir.exists():
            _current.ensure_data_dir()
    return _current
