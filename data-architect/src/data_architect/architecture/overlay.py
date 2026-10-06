"""Warstwa semantyczna przy odczycie: łączy dane od Claude'a (baza) z poprawkami użytkownika (overrides.json).

Zasada pierwszeństwa: użytkownik > Claude > automat. Każda funkcja zwraca też „origin”, czyli skąd pochodzi
wartość (user, claude, auto), żeby UI mogło oznaczyć wartości poprawione ręcznie.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from data_architect.architecture.auto_domains import auto_domain_id
from data_architect.core.models import DbObject, Domain, Issue, Note, Semantic
from data_architect.core.project_files import Overrides


def domains(s: Session, ov: Overrides) -> dict[str, dict]:
    """Wszystkie domeny (id → słownik) z nazwą i opisem poprawionymi przez użytkownika."""
    result = {}
    for d in s.scalars(select(Domain).order_by(Domain.sort, Domain.name)):
        user = ov.domains.get(d.id)
        result[d.id] = {
            "id": d.id,
            "project": d.project,
            "name": user.name if user and user.name else d.name,
            "description": user.description if user and user.description else d.description,
            "origin": d.origin,
            "nameOrigin": "user" if user and user.name else d.origin,
            "descOrigin": "user" if user and user.description else d.origin,
            "sort": d.sort,
            "color": d.color,
        }
    return result


def semantic_of(object_id: str, sem: Semantic | None, ov: Overrides) -> dict:
    """Domena i opis obiektu: {domain, domainOrigin, description, descOrigin}."""
    user = ov.objects.get(object_id)
    user_domain = user.domain if user else None
    user_description = user.description if user else None
    claude_description = sem.description if sem else None

    if user_description:
        desc_origin = "user"
    elif claude_description:
        desc_origin = sem.origin
    else:
        desc_origin = None

    return {
        "domain": user_domain or (sem.domain_id if sem else None),
        "domainOrigin": "user" if user_domain else (sem.origin if sem else None),
        "description": user_description or claude_description,
        "descOrigin": desc_origin,
    }


def effective_domain(obj: DbObject, semantic: dict, known_domains: dict) -> str:
    """Domena, w której obiekt jest pokazywany; gdy przypisana nie istnieje, domena automatyczna wg schematu."""
    return semantic["domain"] if semantic["domain"] in known_domains else auto_domain_id(obj.project, obj.schema_name)


def note_of(s: Session, ov: Overrides, target: str) -> dict:
    """Opis bazy („project:Nazwa”) lub systemu zewnętrznego („ext|nazwa”): {description, origin, name}."""
    user = ov.notes.get(target)
    note = s.get(Note, target)
    if user and user.description:
        return {"description": user.description, "origin": "user", "name": user.name}
    return {
        "description": note.description if note else None,
        "origin": note.origin if note else None,
        "name": user.name if user else None,
    }


def issue_status(issue: Issue, ov: Overrides) -> dict:
    """Status pozycji „do wyjaśnienia”: open, done albo skip. Opisanie systemu zewnętrznego zamyka jego pozycje."""
    user = ov.issues.get(issue.key)
    if user and user.status:
        return {"status": user.status, "resolution": user.resolution, "by": "user"}

    if issue.kind == "external" and issue.ext_id:
        note = ov.notes.get(issue.ext_id)
        if note and note.description:
            return {"status": "done", "resolution": f"Opisano system: {note.description}", "by": "user"}

    return {"status": "open", "resolution": None, "by": None}


def all_semantics(s: Session) -> dict[str, Semantic]:
    """Semantyka wszystkich obiektów (object_id → wiersz), do widoków listujących wiele obiektów naraz."""
    return {sem.object_id: sem for sem in s.scalars(select(Semantic))}
