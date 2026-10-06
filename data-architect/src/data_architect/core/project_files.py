"""Pliki wtyczki w repozytorium projektu (katalog .claude/data-architect).

    overrides.json   poprawki użytkownika z UI (mają pierwszeństwo przed Claude'em i silnikiem)
    guidelines.md    wskazówki interpretacji projektu z wywiadu przy /ssdt-atlas:init
    config.json      ustawienia analizy, np. projekty do pominięcia
    .gitignore       wyklucza lokalne pliki (baza, log, katalog roboczy)

Pliki są przeznaczone do commitowania, więc zapis jest deterministyczny (posortowane klucze, wcięcia).
"""

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


def now_iso() -> str:
    """Bieżący czas UTC w formacie ISO 8601 (z milisekundami), używany we wszystkich znacznikach czasu."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Model poprawek użytkownika (overrides.json)
# ---------------------------------------------------------------------------


class _Entry(BaseModel):
    """Wspólna baza wpisów: nieznane pola z pliku są zachowywane, `updatedAt` mówi, kiedy zmieniono wpis."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    updatedAt: str | None = None  # noqa: N815 — nazwa pola w pliku JSON


class ObjectOverride(_Entry):
    domain: str | None = None
    description: str | None = None


class DomainOverride(_Entry):
    name: str | None = None
    description: str | None = None


class NoteOverride(_Entry):
    """Opis bazy („project:Nazwa”) lub systemu zewnętrznego („ext|nazwa”) i jego nazwa wyświetlana."""

    name: str | None = None
    description: str | None = None


class IssueOverride(_Entry):
    """Rozstrzygnięcie pozycji „do wyjaśnienia”: status done/skip i opis."""

    status: str | None = None
    resolution: str | None = None
    target: str | None = None


class EdgeOverride(BaseModel):
    """Relacja dodana przez użytkownika (np. po zatwierdzeniu propozycji Claude'a)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    source: str = Field(alias="from")
    to: str
    kind: str = "reads"
    note: str | None = None
    addedAt: str | None = None  # noqa: N815


class Overrides(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: int = 1
    objects: dict[str, ObjectOverride] = {}
    domains: dict[str, DomainOverride] = {}
    notes: dict[str, NoteOverride] = {}
    edges: list[EdgeOverride] = []
    issues: dict[str, IssueOverride] = {}

    def add_edge(self, source: str, to: str, kind: str, note: str) -> None:
        """Dodaje relację użytkownika, jeśli takiej jeszcze nie ma."""
        if not any(e.source == source and e.to == to and e.kind == kind for e in self.edges):
            self.edges.append(EdgeOverride(source=source, to=to, kind=kind, note=note, addedAt=now_iso()))


# sekcja overrides.json → klasa wpisu (do ustawiania pojedynczych pól)
_SECTIONS: dict[str, type[_Entry]] = {
    "objects": ObjectOverride,
    "domains": DomainOverride,
    "notes": NoteOverride,
    "issues": IssueOverride,
}


# ---------------------------------------------------------------------------
# Dostęp do plików
# ---------------------------------------------------------------------------


def prepare_data_dir(data_dir: Path) -> None:
    """Zakłada katalog danych wtyczki z .gitignore dla plików lokalnych. Wołane dopiero przed pierwszym zapisem,
    więc sama sesja Claude Code w projekcie niezwiązanym z SSDT niczego nie tworzy."""
    data_dir.mkdir(parents=True, exist_ok=True)
    gitignore = data_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "# Lokalne dane SSDT Atlas (odtwarzalne)\natlas.db*\nwork/\nui.lock\natlas.log\n", encoding="utf-8"
        )


class ProjectFiles:
    def __init__(self, data_dir: Path, prepare_dir: Callable[[], None] | None = None):
        self.dir = data_dir
        self.overrides_file = data_dir / "overrides.json"
        self.guidelines_file = data_dir / "guidelines.md"
        self.config_file = data_dir / "config.json"

        # przygotowanie katalogu przed zapisem (Workspace podaje własne, które włącza też log do pliku)
        self._prepare_dir = prepare_dir or (lambda: prepare_data_dir(data_dir))

        # odczyt–modyfikacja–zapis overrides.json z wielu wątków (MCP i HTTP) wykonujemy po kolei
        self._lock = threading.RLock()

    # --- overrides.json --------------------------------------------------

    def overrides(self) -> Overrides:
        """Aktualne poprawki użytkownika. Plik czytamy za każdym razem: mógł go zmienić git albo inna sesja."""
        if not self.overrides_file.exists():
            return Overrides()
        try:
            return Overrides.model_validate_json(self.overrides_file.read_text(encoding="utf-8"))
        except ValueError:
            return Overrides()

    @contextmanager
    def edit_overrides(self) -> Iterator[Overrides]:
        """Zmiana poprawek: `with files.edit_overrides() as ov: ...` (zapis po wyjściu z bloku)."""
        with self._lock:
            ov = self.overrides()
            yield ov
            self._save_overrides(ov)

    def set_override(self, section: str, key: str, field: str, value: str | None) -> None:
        """Ustawia jedno pole poprawki, np. ("objects", "Sales|sales.orders", "description", "…"). Pusta wartość usuwa
        pole.
        """
        with self.edit_overrides() as ov:
            bucket = getattr(ov, section)
            entry = bucket.get(key) or _SECTIONS[section]()
            setattr(entry, field, value or None)

            # wpis bez żadnej wartości znika z pliku
            values = entry.model_dump(exclude={"updatedAt"}, exclude_none=True)
            if values:
                entry.updatedAt = now_iso()
                bucket[key] = entry
            else:
                bucket.pop(key, None)

    def _save_overrides(self, ov: Overrides) -> None:
        self._prepare_dir()
        data = ov.model_dump(by_alias=True, exclude_none=True)
        for section in _SECTIONS:
            data[section] = dict(sorted(data[section].items()))
        self.overrides_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # --- guidelines.md ---------------------------------------------------

    def guidelines(self) -> str | None:
        return self.guidelines_file.read_text(encoding="utf-8") if self.guidelines_file.exists() else None

    def save_guidelines(self, markdown: str) -> None:
        self._prepare_dir()
        text = markdown if markdown.endswith("\n") else markdown + "\n"
        self.guidelines_file.write_text(text, encoding="utf-8")

    # --- config.json -----------------------------------------------------

    def config(self) -> dict:
        try:
            return {"excludeProjects": [], **json.loads(self.config_file.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            return {"excludeProjects": []}

    def excluded_projects(self) -> set[str]:
        """Nazwy projektów do pominięcia (małymi literami)."""
        return {name.lower() for name in self.config().get("excludeProjects") or []}

    def save_config(self, config: dict) -> None:
        self._prepare_dir()
        self.config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
