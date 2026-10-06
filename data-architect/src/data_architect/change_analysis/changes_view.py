"""Zestaw zmian: widok dla UI (zakładka „Zmiany”) i zwięzła wersja dla Claude'a (atlas_refresh, atlas_changes).

Każda zmiana to „było → jest” jednego obiektu; dla UI dołączamy różnicę definicji SQL liczoną z dwóch zapisanych
analiz (definition_diff.py). Commity od poprzedniej analizy są osobną listą, bez wiązania z obiektami.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from data_architect.architecture.map_view import map_model
from data_architect.architecture.overlay import all_semantics, domains, effective_domain, semantic_of
from data_architect.change_analysis.definition_diff import definition_diff
from data_architect.core.database import snapshot_info
from data_architect.core.models import Change, Changeset, DbObject, Definition, Snapshot
from data_architect.core.project_files import Overrides
from data_architect.core.settings import TYPE_LETTER

RISK_ORDER = {"high": 0, "med": 1, "low": 2}

# pola szczegółów zmiany przekazywane Claude'owi (tylko niepuste)
DETAIL_FIELDS = (
    "columnsAdded", "columnsRemoved", "columnsChanged", "paramsAdded", "paramsRemoved",
    "depsAdded", "depsRemoved", "dependents", "brokenBy", "newIssues",
)  # fmt: skip


def find_changeset(s: Session, changeset_id: int | None = None) -> Changeset | None:
    """Wskazany zestaw zmian albo ostatni."""
    if changeset_id:
        return s.get(Changeset, changeset_id)
    return s.scalars(select(Changeset).order_by(Changeset.id.desc()).limit(1)).first()


def changeset_view(s: Session, ov: Overrides, changeset_id: int | None = None, with_diff: bool = True) -> dict | None:
    """Zestaw zmian dla UI: zmiany obiektów od najwyższego ryzyka, z różnicą definicji „było → jest”."""
    changeset = find_changeset(s, changeset_id)
    if not changeset:
        return None

    known_domains = domains(s, ov)
    semantics = all_semantics(s)
    objects_after = _objects(s, changeset.to_snapshot)
    objects_before = _objects(s, changeset.from_snapshot)

    changes = []
    for c in s.scalars(select(Change).where(Change.changeset_id == changeset.id)):
        details = c.details or {}
        obj = objects_after.get(c.object_id) or objects_before.get(c.object_id)
        domain_id = effective_domain(obj, semantic_of(c.object_id, semantics.get(c.object_id), ov), known_domains)
        old = objects_before.get(details.get("oldId")) if details.get("oldId") else None

        entry = {
            "objectId": c.object_id,
            "project": obj.project,
            "name": f"{obj.schema_name}.{obj.name}",
            "oldName": f"{old.schema_name}.{old.name}" if old else None,
            "t": TYPE_LETTER.get(obj.type),
            "type": c.change_type,
            "risk": c.risk,
            "summary": c.summary,
            "details": details,
            "domain": domain_id,
            "domainName": known_domains[domain_id]["name"] if domain_id in known_domains else obj.schema_name,
        }
        if with_diff:
            entry["diff"] = definition_diff(_text(s, details.get("hashFrom")), _text(s, details.get("hashTo")))
        changes.append(entry)

    changes.sort(key=lambda x: (RISK_ORDER.get(x["risk"], 3), (x["domainName"] or "").lower(), x["name"].lower()))

    return {
        "id": changeset.id,
        "from": snapshot_info(s.get(Snapshot, changeset.from_snapshot)),
        "to": snapshot_info(s.get(Snapshot, changeset.to_snapshot)),
        "createdAt": changeset.created_at,
        "summary": changeset.summary,
        "reviewed": bool(changeset.reviewed),
        "stats": changeset.stats or {},
        "changes": changes,
    }


def changeset_for_claude(s: Session, ov: Overrides, changeset_id: int) -> dict:
    """Zwięzła wersja zestawu zmian dla Claude'a: bez różnic definicji, tylko niepuste szczegóły i następne kroki."""
    view = changeset_view(s, ov, changeset_id, with_diff=False)
    model = map_model(s, ov)

    # commity: tylko kontekst („co działo się w repozytorium”), bez wiązania z obiektami
    commits = [
        f"{c['sha'][:7]} {c['date'][:10]} {c['author']}: {c['message']}"
        for c in (view["stats"].get("commitList") or [])[:40]
    ]

    changes = []
    for c in view["changes"]:
        entry = {
            "object": c["objectId"],
            "name": c["name"],
            "type": c["type"],
            "risk": c["risk"],
            "domain": c["domainName"],
        }
        if c["oldName"]:
            entry["oldName"] = c["oldName"]
        entry |= {k: c["details"][k] for k in DETAIL_FIELDS if c["details"].get(k)}
        changes.append(entry)

    new_ids = {c["objectId"] for c in view["changes"] if c["type"] in ("added", "renamed")}
    return {
        "changeset": view["id"],
        "from": view["from"],
        "to": view["to"],
        "stats": {k: v for k, v in view["stats"].items() if k != "commitList"},
        "commits": commits,
        "changes": changes,
        "newObjectsInAutoDomains": [
            o["id"] for o in model["objects"] if o["domain"].startswith("auto|") and o["id"] in new_ids
        ],
        "openIssues": model["openIssues"],
        "next": (
            "Przejrzyj zmiany (atlas_get_object dla istotnych), zapisz podsumowanie atlas_save_change_notes, "
            "przypisz domeny nowym obiektom (atlas_assign) i zaproponuj wyjaśnienia nowych pozycji (atlas_propose)."
        ),
    }


def _objects(s: Session, sid: int) -> dict[str, DbObject]:
    return {o.id: o for o in s.scalars(select(DbObject).where(DbObject.snapshot_id == sid))}


def _text(s: Session, definition_hash: str | None) -> str | None:
    """Tekst definicji o danym hashu (None dla nowego albo usuniętego obiektu)."""
    definition = s.get(Definition, definition_hash) if definition_hash else None
    return definition.text if definition else None
