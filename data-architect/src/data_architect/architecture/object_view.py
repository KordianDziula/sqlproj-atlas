"""Szczegóły obiektu (panel w UI, narzędzie atlas_get_object) i analiza wpływu (atlas_impact)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from data_architect.architecture.map_view import map_model
from data_architect.architecture.overlay import domains, effective_domain, issue_status, semantic_of
from data_architect.core.database import current_snapshot
from data_architect.core.models import Change, Changeset, DbObject, Definition, Issue, Semantic
from data_architect.core.project_files import Overrides
from data_architect.core.settings import type_letter


def object_detail(s: Session, ov: Overrides, object_id: str) -> dict | None:
    """Kolumny, parametry, pełna definicja, relacje w obie strony, problemy i notatki o zmianach."""
    snapshot = current_snapshot(s)
    obj = s.get(DbObject, (snapshot.id, object_id)) if snapshot else None
    if not obj:
        return None

    semantic = semantic_of(object_id, s.get(Semantic, object_id), ov)
    known_domains = domains(s, ov)
    domain_id = effective_domain(obj, semantic, known_domains)
    definition = s.get(Definition, obj.hash)

    # --- relacje: z modelu mapy, żeby uwzględnić relacje użytkownika i propozycje Claude'a ---
    model = map_model(s, ov)
    objects = {o["id"]: o for o in model["objects"]}
    externals = {x["id"]: x for x in model["ext"]}

    def ref(other_id: str) -> dict:
        if other_id in objects:
            o = objects[other_id]
            return {
                "id": other_id,
                "name": f"{o['schema']}.{o['name']}",
                "project": o["project"],
                "t": o["t"],
                "domain": o["domain"],
            }
        if other_id in externals:
            return {"id": other_id, "name": externals[other_id]["name"], "ext": True}
        return {"id": other_id, "name": other_id}

    def relation(edge: dict, other_end: str) -> dict:
        return ref(edge[other_end]) | {"kind": edge["kind"], "origin": edge["origin"], "unsure": edge["unsure"]}

    # --- problemy i notatki o zmianach ---
    issues = s.scalars(select(Issue).where(Issue.snapshot_id == snapshot.id, Issue.object_id == object_id))
    changes = s.scalars(select(Change).where(Change.object_id == object_id).order_by(Change.changeset_id.desc()))

    return {
        "id": object_id,
        "project": obj.project,
        "schema": obj.schema_name,
        "name": obj.name,
        "type": obj.type,
        "t": type_letter(obj.type),
        "file": obj.file,
        "line": obj.line,
        "columns": obj.columns,
        "params": obj.params,
        "definition": definition.text if definition else "",
        "domain": domain_id,
        "domainName": known_domains[domain_id]["name"] if domain_id in known_domains else obj.schema_name,
        "domainOrigin": semantic["domainOrigin"],
        "description": semantic["description"],
        "descOrigin": semantic["descOrigin"],
        "uses": [relation(e, "dst") for e in model["edges"] if e["src"] == object_id],
        "usedBy": [relation(e, "src") for e in model["edges"] if e["dst"] == object_id],
        "issues": [_issue_row(i) | issue_status(i, ov) for i in issues],
        "changeNotes": [
            {
                "changeset": c.changeset_id,
                "date": s.get(Changeset, c.changeset_id).created_at,  # kiedy odświeżono analizę
                "type": c.change_type,
                "summary": c.summary,
                "risk": c.risk,
            }
            for c in changes
        ],
    }


def impact(s: Session, ov: Overrides, object_id: str, depth: int = 5) -> list[dict]:
    """Obiekty zależne przechodnio od `object_id` (do `depth` poziomów): [{id, depth, via, kind}]."""
    incoming: dict[str, list[dict]] = {}
    for e in map_model(s, ov)["edges"]:
        incoming.setdefault(e["dst"], []).append(e)

    found, seen, frontier = [], {object_id}, [object_id]
    for level in range(1, depth + 1):
        next_frontier = []
        for current in frontier:
            for e in incoming.get(current, []):
                if e["src"] in seen:
                    continue
                seen.add(e["src"])
                next_frontier.append(e["src"])
                found.append({"id": e["src"], "depth": level, "via": current, "kind": e["kind"]})
        frontier = next_frontier
        if not frontier:
            break

    return found


def _issue_row(issue: Issue) -> dict:
    return {
        "snapshot_id": issue.snapshot_id,
        "key": issue.key,
        "object_id": issue.object_id,
        "kind": issue.kind,
        "ref": issue.ref,
        "ext_id": issue.ext_id,
        "line": issue.line,
        "snippet": issue.snippet,
        "message": issue.message,
    }
