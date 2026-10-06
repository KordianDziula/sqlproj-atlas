"""Połączenie z bazą SQLite i podstawowe zapytania wspólne dla całej wtyczki."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from data_architect.core.models import Base, Meta, Snapshot


class Database:
    """Baza jednego projektu. Każda operacja otwiera własną sesję (`with db.session() as s:`).

    Z bazy korzystają równolegle wątki narzędzi MCP i wątki serwera HTTP. SQLite w trybie WAL
    pozwala czytać w trakcie zapisu, a zapisy czekają na siebie (busy timeout).
    """

    def __init__(self, path: Path):
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        event.listen(self.engine, "connect", _configure_sqlite)

        Base.metadata.create_all(self.engine)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Sesja w transakcji: zatwierdzana na końcu bloku, wycofywana przy wyjątku."""
        with self._sessions.begin() as s:
            yield s

    def dispose(self) -> None:
        self.engine.dispose()


def _configure_sqlite(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA busy_timeout = 30000")
    cursor.close()


# ---------------------------------------------------------------------------
# Zapytania wspólne
# ---------------------------------------------------------------------------


def current_snapshot(s: Session) -> Snapshot | None:
    """Ostatnia analiza projektu (None, gdy projekt nie był jeszcze analizowany)."""
    return s.scalars(select(Snapshot).where(Snapshot.kind == "analysis").order_by(Snapshot.id.desc()).limit(1)).first()


def revision(s: Session) -> int:
    """Licznik zmian danych: UI odpytuje go co kilka sekund i przeładowuje widok, gdy wzrośnie."""
    meta = s.get(Meta, "rev")
    return int(meta.v) if meta and meta.v else 0


def bump_revision(s: Session) -> int:
    """Zwiększa licznik zmian; wywoływane przy każdym zapisie widocznym w UI."""
    rev = revision(s) + 1
    s.merge(Meta(k="rev", v=str(rev)))
    return rev


def snapshot_info(snapshot: Snapshot | None) -> dict | None:
    """Opis analizy dla UI i narzędzi: commit, autor, data, czy były zmiany lokalne."""
    if not snapshot:
        return None
    sha = snapshot.commit_sha
    return {
        "id": snapshot.id,
        "sha": sha,
        "shortSha": sha[:7] if sha else None,
        "date": snapshot.commit_date,
        "author": snapshot.author,
        "message": snapshot.message,
        "kind": snapshot.kind,
        "dirty": bool(snapshot.dirty),
        "createdAt": snapshot.created_at,
        "label": snapshot.label,
        "stats": snapshot.stats,
    }
