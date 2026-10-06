"""Wyszukiwanie obiektów po id podanym przez Claude'a lub użytkownika."""

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from data_architect.core.database import current_snapshot
from data_architect.core.errors import AtlasError
from data_architect.core.models import DbObject, Snapshot

NO_ANALYSIS = "Brak analizy. Najpierw uruchom atlas_analyze (lub skill /ssdt-atlas:init)."


def require_analysis(s: Session) -> Snapshot:
    """Ostatnia analiza; błąd z podpowiedzią, gdy projekt nie był jeszcze analizowany."""
    snapshot = current_snapshot(s)
    if not snapshot:
        raise AtlasError(NO_ANALYSIS)
    return snapshot


def resolve_object_id(s: Session, raw: str | None) -> str:
    """Pełne id obiektu z „Projekt|schemat.nazwa”, „schemat.nazwa” albo samej nazwy (jeśli jednoznaczna).

    Wielkość liter i nawiasy kwadratowe nie mają znaczenia; bez schematu zakładamy też „dbo”.
    """
    if not raw:
        raise AtlasError("Brak id obiektu")

    snapshot = require_analysis(s)
    ids = list(s.scalars(select(DbObject.id).where(DbObject.snapshot_id == snapshot.id)))
    query = re.sub(r"[\[\]]", "", str(raw).strip()).lower()

    # 1. dokładne id
    exact = next((i for i in ids if i.lower() == query), None)
    if exact:
        return exact

    # 2. „schemat.nazwa” w dowolnym projekcie (bez schematu: dbo)
    key = query if "." in query else "dbo." + query
    by_key = [i for i in ids if i.split("|", 1)[1] in (query, key)]
    if len(by_key) == 1:
        return by_key[0]

    # 3. sama nazwa obiektu
    by_name = [i for i in ids if i.split("|", 1)[1].split(".", 1)[-1] == query]
    if len(by_name) == 1:
        return by_name[0]

    candidates = by_key or by_name
    if len(candidates) > 1:
        raise AtlasError(f"Niejednoznaczne id „{raw}”: {', '.join(candidates)}")
    raise AtlasError(f"Nie znaleziono obiektu „{raw}”")


def slug(text: str) -> str:
    """Fragment id z nazwy: „Słowniki i konfiguracja” → „slowniki-i-konfiguracja”."""
    ascii_text = unicodedata.normalize("NFKD", text.lower().replace("ł", "l")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
