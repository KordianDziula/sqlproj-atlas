"""Porównanie dwóch snapshotów: które obiekty dodano, zmieniono, usunięto lub przemianowano i z jakim ryzykiem.

Obiekt jest „zmieniony”, gdy zmienił się hash jego definicji (silnik dokleja do tabeli także jej indeksy).
Zmianę nazwy rozpoznajemy po wpisie w .refactorlog albo po identycznej definicji pod nową nazwą.

Ryzyko:
    high  inne obiekty przestały się rozwiązywać (np. usunięta kolumna używana w widoku),
    med   nowe pozycje do wyjaśnienia (np. dynamiczny SQL), usunięte kolumny, usunięty obiekt z zależnymi,
    low   pozostałe.
"""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ssdt_atlas.core.models import DbObject, Edge, Issue, Project


@dataclass
class ObjectChange:
    object_id: str
    change_type: str  # added, modified, removed, renamed
    details: dict
    risk: str


@dataclass
class _Side:
    """Jedna strona porównania: obiekty i relacje snapshotu."""

    objects: dict[str, DbObject]
    outgoing: dict[str, list[Edge]]
    incoming: dict[str, list[Edge]]

    @classmethod
    def load(cls, s: Session, sid: int) -> "_Side":
        objects = {o.id: o for o in s.scalars(select(DbObject).where(DbObject.snapshot_id == sid))}
        outgoing: dict[str, list[Edge]] = {}
        incoming: dict[str, list[Edge]] = {}
        for e in s.scalars(select(Edge).where(Edge.snapshot_id == sid)):
            outgoing.setdefault(e.src, []).append(e)
            incoming.setdefault(e.dst, []).append(e)
        return cls(objects, outgoing, incoming)


def compare_snapshots(s: Session, from_id: int, to_id: int) -> list[ObjectChange]:
    old, new = _Side.load(s, from_id), _Side.load(s, to_id)

    # pozycje do wyjaśnienia, których nie było w poprzedniej analizie
    old_issue_keys = set(s.scalars(select(Issue.key).where(Issue.snapshot_id == from_id)))
    new_issues = [i for i in s.scalars(select(Issue).where(Issue.snapshot_id == to_id)) if i.key not in old_issue_keys]

    renames = _refactorlog_renames(s, to_id)
    removed = dict.fromkeys(oid for oid in old.objects if oid not in new.objects)  # zbiór z zachowaniem kolejności
    changes = []

    # --- obiekty obecne w nowej analizie ---
    for oid, obj in new.objects.items():
        before = old.objects.get(oid)

        if before is None:
            old_id = _renamed_from(obj, renames.get(oid), removed, old.objects)
            if old_id:
                del removed[old_id]
                changes.append(_describe("renamed", obj, old.objects[old_id], old, new, new.incoming, new_issues))
            else:
                changes.append(_describe("added", obj, None, old, new, new.incoming, new_issues))

        elif before.hash != obj.hash:
            changes.append(_describe("modified", obj, before, old, new, new.incoming, new_issues))

    # --- obiekty, których już nie ma ---
    for oid in removed:
        changes.append(_describe("removed", None, old.objects[oid], old, new, old.incoming, new_issues))

    return changes


# ---------------------------------------------------------------------------
# Szczegóły jednej zmiany
# ---------------------------------------------------------------------------


def _describe(kind: str, after: DbObject | None, before: DbObject | None, old: _Side, new: _Side,
              incoming: dict[str, list[Edge]], new_issues: list[Issue]) -> ObjectChange:  # fmt: skip
    current = after or before
    oid = current.id
    details: dict = {
        "type": current.type,
        "file": current.file,
        "oldFile": before.file if before else None,
        "hashFrom": before.hash if before else None,
        "hashTo": after.hash if after else None,
    }
    if kind == "renamed":
        details["oldId"] = before.id

    # --- kolumny, parametry i zależności (dla zmienionych i przemianowanych) ---
    if before and after:
        details.update(_diff_named(before.columns, after.columns, "columns"))
        details["columnsChanged"] = _changed_columns(before.columns, after.columns)
        details.update(_diff_named(before.params, after.params, "params"))

        deps_before = _dependencies(old.outgoing.get(before.id, []))
        deps_after = _dependencies(new.outgoing.get(after.id, []))
        details["depsAdded"] = [d for d in deps_after if d not in deps_before]
        details["depsRemoved"] = [d for d in deps_before if d not in deps_after]

    if kind == "added":
        details["depsAdded"] = _dependencies(new.outgoing.get(oid, []))

    # --- wpływ: kto korzysta z obiektu (dla usuniętego tylko obiekty, które nadal istnieją) ---
    dependents = [e.src for e in incoming.get(oid, [])]
    if kind == "removed":
        dependents = [d for d in dependents if d in new.objects]
    details["dependents"] = list(dict.fromkeys(dependents))

    # --- nowe problemy: odwołania do tego obiektu (lub jego kolumn), które przestały się rozwiązywać ---
    project, key = oid.split("|", 1)
    old_key = before.key if before else key

    def hits(ref: str) -> bool:
        return ref in (key, old_key, f"{project}.{key}") or ref.startswith((key + ".", old_key + "."))

    details["brokenBy"] = [
        {"object": i.object_id, "ref": i.ref, "line": i.line, "message": i.message}
        for i in new_issues
        if i.kind == "unresolved" and hits(i.ref or "")
    ]
    own_issues = [i for i in new_issues if i.object_id == oid]
    details["newDynamic"] = sum(1 for i in own_issues if i.kind == "dynamic")
    details["newIssues"] = [{"kind": i.kind, "ref": i.ref, "message": i.message} for i in own_issues]

    return ObjectChange(object_id=oid, change_type=kind, details=details, risk=_risk(kind, details))


def _risk(kind: str, details: dict) -> str:
    if details["brokenBy"]:
        return "high"
    if details["newDynamic"] or details["newIssues"] or details.get("columnsRemoved"):
        return "med"
    if kind == "removed" and details["dependents"]:
        return "med"
    return "low"


def _diff_named(before: list[dict] | None, after: list[dict] | None, prefix: str) -> dict:
    """Dodane i usunięte elementy listy (kolumny albo parametry) porównywane po nazwie bez wielkości liter."""
    old = {c["name"].lower(): c["name"] for c in before or []}
    new = {c["name"].lower(): c["name"] for c in after or []}
    return {
        f"{prefix}Added": [name for k, name in new.items() if k not in old],
        f"{prefix}Removed": [name for k, name in old.items() if k not in new],
    }


def _changed_columns(before: list[dict] | None, after: list[dict] | None) -> list[str]:
    """Kolumny, którym zmienił się typ albo dopuszczalność NULL, np. „Price: decimal(10,2) → decimal(18,2)”."""
    old = {c["name"].lower(): c for c in before or []}
    changed = []
    for c in after or []:
        prev = old.get(c["name"].lower())
        if prev and (prev.get("type") != c.get("type") or prev.get("nullable") != c.get("nullable")):
            changed.append(f"{c['name']}: {prev.get('type')} → {c.get('type')}")
    return changed


def _dependencies(edges: list[Edge]) -> list[str]:
    """Zależności obiektu jako „cel|rodzaj”, bez powtórzeń."""
    return list(dict.fromkeys(f"{e.dst}|{e.kind}" for e in edges))


# ---------------------------------------------------------------------------
# Zmiany nazw
# ---------------------------------------------------------------------------


def _refactorlog_renames(s: Session, sid: int) -> dict[str, str]:
    """Zmiany nazw z .refactorlog: nowe id → stare id."""
    renames = {}
    for project in s.scalars(select(Project).where(Project.snapshot_id == sid)):
        for r in (project.info or {}).get("renames") or []:
            old = re.sub(r"[\[\]]", "", r["element"]).lower()
            parts = old.split(".")
            if len(parts) < 2:
                continue
            new_name = re.sub(r"[\[\]]", "", r["newName"]).lower()
            new_key = f"{parts[0]}.{new_name}"
            renames[f"{project.name}|{new_key}"] = f"{project.name}|{old}"
    return renames


def _renamed_from(
    obj: DbObject, refactor_old: str | None, removed: dict, old_objects: dict[str, DbObject]
) -> str | None:
    """Stare id, jeśli nowy obiekt to zmiana nazwy: wpis w .refactorlog albo usunięty obiekt z identyczną definicją."""
    if refactor_old in removed:
        return refactor_old

    for old_id in removed:
        before = old_objects[old_id]
        if before.hash == obj.hash and before.project == obj.project and before.type == obj.type:
            return old_id

    return None
