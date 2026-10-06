"""Model bazy SQLite (SQLAlchemy ORM).

Baza `.claude/data-architect/atlas.db` jest lokalna (w .gitignore) i dzieli się na trzy grupy tabel:

1. Snapshoty: wynik jednej analizy silnikiem, niezmienny po zapisie.
   Snapshot → Project, DbObject, Edge, Issue, External; definicje SQL w Definition (współdzielone po hashu).
2. Semantyka od Claude'a: Domain, Semantic (domena i opis obiektu), Note (opis bazy lub systemu
   zewnętrznego), Proposal (propozycja wyjaśnienia pozycji „do wyjaśnienia”).
3. Zmiany: Changeset (porównanie dwóch snapshotów z listą commitów) → Change (zmiana jednego obiektu).

Poprawki użytkownika NIE są w bazie, tylko w overrides.json (storage/project_files.py), bo trafiają do repozytorium.
Nazwy tabel i kolumn są zgodne z wcześniejszymi wersjami wtyczki, więc istniejące bazy działają bez migracji.
"""

from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# 1. Snapshoty (wynik analizy)
# ---------------------------------------------------------------------------


class Snapshot(Base):
    """Jedna analiza: stan projektu w danym commicie (oraz czy były niezatwierdzone zmiany)."""

    __tablename__ = "snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    commit_sha: Mapped[str | None] = mapped_column(String)
    commit_date: Mapped[str | None] = mapped_column(String)
    author: Mapped[str | None] = mapped_column(String)
    message: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String, default="analysis")
    dirty: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str | None] = mapped_column(String)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    label: Mapped[str | None] = mapped_column(String)


class Project(Base):
    """Projekt .sqlproj (baza danych) w snapshocie; `info` to metadane z pliku projektu."""

    __tablename__ = "project"

    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshot.id"), primary_key=True)
    name: Mapped[str] = mapped_column(String, primary_key=True)
    file: Mapped[str] = mapped_column(String)
    format: Mapped[str] = mapped_column(String)  # "sdk" albo "classic"
    platform: Mapped[str | None] = mapped_column(String)
    info: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Definition(Base):
    """Tekst definicji SQL; ten sam hash w wielu snapshotach zapisujemy raz."""

    __tablename__ = "definition"

    hash: Mapped[str] = mapped_column(String, primary_key=True)
    text: Mapped[str] = mapped_column(Text, default="")


class DbObject(Base):
    """Obiekt bazy (tabela, widok, procedura, ...). Id: „Projekt|schemat.nazwa” małymi literami po „|”."""

    __tablename__ = "obj"

    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshot.id"), primary_key=True)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    project: Mapped[str] = mapped_column(String)
    schema_name: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)
    file: Mapped[str | None] = mapped_column(String)
    line: Mapped[int | None] = mapped_column(Integer)
    hash: Mapped[str] = mapped_column(String)
    columns: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    params: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)

    @property
    def key(self) -> str:
        """Część id po nazwie projektu, np. „sales.orders”."""
        return self.id.split("|", 1)[1]


class Edge(Base):
    """Relacja między obiektami: reads, writes, calls, fk, on (trigger) itd.

    `dst` może być systemem zewnętrznym („ext|…”).
    """

    __tablename__ = "edge"

    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshot.id"), primary_key=True)
    src: Mapped[str] = mapped_column(String, primary_key=True)
    dst: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    origin: Mapped[str] = mapped_column(String, default="engine")
    unsure: Mapped[int] = mapped_column(Integer, default=0)


class Issue(Base):
    """Pozycja „do wyjaśnienia”: dynamic, unresolved, external, parse."""

    __tablename__ = "issue"

    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshot.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)  # „obiekt|rodzaj|odwołanie”
    object_id: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    ref: Mapped[str | None] = mapped_column(String)
    ext_id: Mapped[str | None] = mapped_column(String)
    line: Mapped[int] = mapped_column(Integer, default=0)
    snippet: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)


class External(Base):
    """System zewnętrzny: baza spoza solucji, linked server, zmienna SQLCMD bez projektu."""

    __tablename__ = "ext"

    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshot.id"), primary_key=True)
    id: Mapped[str] = mapped_column(String, primary_key=True)  # „ext|nazwa”
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)


# ---------------------------------------------------------------------------
# 2. Semantyka od Claude'a (niezależna od snapshotów)
# ---------------------------------------------------------------------------


class Domain(Base):
    """Domena biznesowa w obrębie jednej bazy. origin: auto (wg schematu), claude, user."""

    __tablename__ = "domain"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # „Projekt|slug” albo „auto|Projekt|schemat”
    project: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[int | None] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String)
    sort: Mapped[int] = mapped_column(Integer, default=100)


class Semantic(Base):
    """Domena i opis obiektu nadane przez Claude'a (albo automatycznie wg schematu)."""

    __tablename__ = "sem"

    object_id: Mapped[str] = mapped_column(String, primary_key=True)
    domain_id: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[str | None] = mapped_column(String)


class Note(Base):
    """Opis bazy („project:Nazwa”) albo systemu zewnętrznego („ext|nazwa”) od Claude'a."""

    __tablename__ = "note"

    target: Mapped[str] = mapped_column(String, primary_key=True)
    description: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[str | None] = mapped_column(String)


class Proposal(Base):
    """Propozycja Claude'a dla pozycji „do wyjaśnienia”; `targets` to [{id, kind}]."""

    __tablename__ = "proposal"

    issue_key: Mapped[str] = mapped_column(String, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    targets: Mapped[list[dict[str, str]] | None] = mapped_column(JSON)
    confidence: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[str | None] = mapped_column(String)


# ---------------------------------------------------------------------------
# 3. Zmiany między analizami
# ---------------------------------------------------------------------------


class Changeset(Base):
    """Porównanie dwóch snapshotów. `stats` zawiera liczniki i `commitList` (commity od poprzedniej analizy)."""

    __tablename__ = "changeset"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_snapshot: Mapped[int] = mapped_column(ForeignKey("snapshot.id"))
    to_snapshot: Mapped[int] = mapped_column(ForeignKey("snapshot.id"))
    created_at: Mapped[str | None] = mapped_column(String)
    summary: Mapped[str | None] = mapped_column(Text)  # podsumowanie od Claude'a
    reviewed: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Change(Base):
    """Zmiana obiektu: added, modified, removed, renamed; `details` to szczegóły (kolumny, zależności, wpływ)."""

    __tablename__ = "change"

    changeset_id: Mapped[int] = mapped_column(ForeignKey("changeset.id"), primary_key=True)
    object_id: Mapped[str] = mapped_column(String, primary_key=True)
    change_type: Mapped[str] = mapped_column(String)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text)  # opis od Claude'a
    risk: Mapped[str | None] = mapped_column(String)  # high, med, low


class Meta(Base):
    """Pary klucz–wartość, m.in. licznik zmian `rev` odpytywany przez UI."""

    __tablename__ = "meta"

    k: Mapped[str] = mapped_column(String, primary_key=True)
    v: Mapped[str | None] = mapped_column(String)
