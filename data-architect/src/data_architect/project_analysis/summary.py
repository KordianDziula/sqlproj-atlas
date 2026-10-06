"""Podsumowanie analizy dla Claude'a (wynik atlas_analyze): materiał do wywiadu z użytkownikiem przy init."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from data_architect.architecture.issues_view import issues_view
from data_architect.core.database import snapshot_info
from data_architect.core.models import DbObject, Edge, External, Project, Snapshot
from data_architect.core.project_files import Overrides
from data_architect.core.settings import TYPE_LETTER


def analysis_summary(s: Session, ov: Overrides, snapshot_id: int, excluded: list[str]) -> dict:
    """Bazy, schematy, prefiksy nazw, systemy zewnętrzne i problemy w jednym zwięzłym słowniku."""
    objects = list(s.scalars(select(DbObject).where(DbObject.snapshot_id == snapshot_id)))
    edges = list(s.scalars(select(Edge).where(Edge.snapshot_id == snapshot_id)))
    projects = s.scalars(select(Project).where(Project.snapshot_id == snapshot_id))
    externals = s.scalars(select(External).where(External.snapshot_id == snapshot_id))

    # przykłady pozycji do wyjaśnienia według rodzaju
    by_kind: dict[str, list[str]] = {}
    for issue in issues_view(s, ov):
        by_kind.setdefault(issue["kind"], []).append(f"{issue['project']}.{issue['object']}: {issue['ref']}")

    cross_database = [e for e in edges if not e.dst.startswith("ext|") and e.src.split("|")[0] != e.dst.split("|")[0]]

    return {
        "snapshot": snapshot_info(s.get(Snapshot, snapshot_id)),
        "excludedProjects": excluded,
        "projects": [_project_summary(p, objects) for p in projects],
        "namingPrefixes": _naming_prefixes(objects),
        "externalSystems": [
            {"id": x.id, "name": x.name, "kind": x.kind, "usedBy": sorted({e.src for e in edges if e.dst == x.id})}
            for x in externals
        ],
        "crossDatabaseEdges": len(cross_database),
        "issues": {kind: {"count": len(items), "examples": items[:6]} for kind, items in by_kind.items()},
        "totals": {"objects": len(objects), "edges": len(edges)},
    }


def _project_summary(project: Project, objects: list[DbObject]) -> dict:
    info = project.info or {}
    own = [o for o in objects if o.project == project.name]

    by_type: dict[str, int] = {}
    schemas: dict[str, list[str]] = {}
    for o in own:
        letter = TYPE_LETTER.get(o.type, o.type)
        by_type[letter] = by_type.get(letter, 0) + 1
        schemas.setdefault(o.schema_name, []).append(o.name)

    largest_first = sorted(schemas.items(), key=lambda kv: -len(kv[1]))
    return {
        "name": project.name,
        "file": project.file,
        "format": "SDK-style" if project.format == "sdk" else "klasyczny",
        "platform": project.platform,
        "objects": len(own),
        "byType": by_type,
        "schemas": {name: {"count": len(names), "sample": names[:8]} for name, names in largest_first},
        "sqlcmdVariables": info.get("sqlcmd"),
        "references": [
            {k: r.get(k) for k in ("kind", "name", "variable", "literal")} for r in info.get("references") or []
        ],
        "deployScripts": {"pre": info.get("preDeploy"), "post": info.get("postDeploy")},
        "refactorRenames": len(info.get("renames") or []),
        "warnings": (info.get("messages") or [])[:5],
    }


def _naming_prefixes(objects: list[DbObject]) -> dict:
    """Najczęstsze prefiksy nazw (np. usp_, vw_, stg_) z rozkładem typów; podpowiedź do pytań o konwencje."""
    prefixes: dict[str, dict] = {}
    for o in objects:
        match = re.match(r"^([a-z]{1,5})_", o.name, re.IGNORECASE)
        if not match:
            continue

        entry = prefixes.setdefault(match.group(1).lower() + "_", {"count": 0, "types": {}})
        entry["count"] += 1
        letter = TYPE_LETTER.get(o.type, o.type)
        entry["types"][letter] = entry["types"].get(letter, 0) + 1

    return dict(sorted(prefixes.items(), key=lambda kv: -kv[1]["count"])[:15])
