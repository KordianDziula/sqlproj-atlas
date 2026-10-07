"""Model mapy (GET /api/model): bazy, domeny, obiekty, relacje i systemy zewnętrzne ostatniej analizy.

To główna struktura danych UI. Na niej opierają się też narzędzia listujące obiekty i analiza wpływu.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from sqlproj_atlas.architecture.overlay import (
    all_semantics,
    domains,
    effective_domain,
    issue_status,
    note_of,
    semantic_of,
)
from sqlproj_atlas.core.database import current_snapshot, revision, snapshot_info
from sqlproj_atlas.core.models import (
    Change,
    Changeset,
    DbObject,
    Edge,
    External,
    Issue,
    Project,
    Proposal,
)
from sqlproj_atlas.core.project_files import Overrides
from sqlproj_atlas.core.settings import type_letter

DOMAIN_COLORS = 8  # liczba kolorów domen w UI (zmienne --d1 … --d8)


def map_model(s: Session, ov: Overrides) -> dict:
    snapshot = current_snapshot(s)
    if not snapshot:
        return {"initialized": False}
    sid = snapshot.id

    known_domains = domains(s, ov)
    issues = list(s.scalars(select(Issue).where(Issue.snapshot_id == sid)))
    open_issues = _open_issues_by_object(issues, ov)
    changed = _changed_in_latest_changeset(s, sid)

    # --- obiekty z domeną (po poprawkach użytkownika) ---
    semantics = all_semantics(s)
    objects = []
    for obj in s.scalars(select(DbObject).where(DbObject.snapshot_id == sid)):
        semantic = semantic_of(obj.id, semantics.get(obj.id), ov)
        objects.append(
            {
                "id": obj.id,
                "project": obj.project,
                "schema": obj.schema_name,
                "name": obj.name,
                "type": obj.type,
                "t": type_letter(obj.type),
                "domain": effective_domain(obj, semantic, known_domains),
                "warn": open_issues.get(obj.id, 0),
                "changed": changed.get(obj.id),
                "described": bool(semantic["description"]),
            }
        )

    edges = _edges(s, sid, ov, {o["id"] for o in objects}, issues)
    projects = list(s.scalars(select(Project).where(Project.snapshot_id == sid).order_by(Project.name)))
    order = _order_by_data_flow([p.name for p in projects], edges)

    latest = _latest_changeset(s, sid)
    return {
        "initialized": True,
        "rev": revision(s),
        "snapshot": snapshot_info(snapshot),
        "projects": [_project_entry(s, ov, p, objects) for p in sorted(projects, key=lambda p: order.index(p.name))],
        "domains": _domain_list(objects, known_domains, order),
        "objects": objects,
        "edges": edges,
        "ext": _externals(s, ov, sid),
        "openIssues": sum(open_issues.values()),
        "changeset": {"id": latest.id, "count": len(changed)} if latest else None,
        # pominięte w config.json: mapa pokazuje je jako szare kafelki
        "excluded": (snapshot.stats or {}).get("excluded") or {"projects": [], "schemas": {}},
    }


# ---------------------------------------------------------------------------
# Części modelu
# ---------------------------------------------------------------------------


def _open_issues_by_object(issues: list[Issue], ov: Overrides) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        if issue_status(issue, ov)["status"] == "open":
            counts[issue.object_id] = counts.get(issue.object_id, 0) + 1
    return counts


def _latest_changeset(s: Session, sid: int) -> Changeset | None:
    return s.scalars(
        select(Changeset).where(Changeset.to_snapshot == sid).order_by(Changeset.id.desc()).limit(1)
    ).first()


def _changed_in_latest_changeset(s: Session, sid: int) -> dict[str, str]:
    """Obiekty zmienione w ostatnim odświeżeniu (id → rodzaj zmiany), do oznaczenia na mapie."""
    latest = _latest_changeset(s, sid)
    if not latest:
        return {}
    rows = s.execute(select(Change.object_id, Change.change_type).where(Change.changeset_id == latest.id))
    return dict(rows.all())


def _edges(s: Session, sid: int, ov: Overrides, object_ids: set[str], issues: list[Issue]) -> list[dict]:
    """Relacje: z silnika, dodane przez użytkownika oraz niepewne (propozycje Claude'a dla otwartych pozycji)."""
    edges = [
        {"src": e.src, "dst": e.dst, "kind": e.kind, "origin": e.origin, "unsure": bool(e.unsure)}
        for e in s.scalars(select(Edge).where(Edge.snapshot_id == sid))
    ]

    for e in ov.edges:
        if e.source in object_ids and (e.to in object_ids or e.to.startswith("ext|")):
            edges.append({"src": e.source, "dst": e.to, "kind": e.kind or "reads", "origin": "user", "unsure": False})

    issue_by_key = {i.key: i for i in issues}
    for proposal in s.scalars(select(Proposal)):
        issue = issue_by_key.get(proposal.issue_key)
        if not issue or issue_status(issue, ov)["status"] != "open":
            continue
        for target in proposal.targets or []:
            if target["id"] in object_ids:
                edges.append(
                    {
                        "src": issue.object_id,
                        "dst": target["id"],
                        "kind": target.get("kind") or "reads",
                        "origin": "claude",
                        "unsure": True,
                    }
                )

    return edges


def _order_by_data_flow(names: list[str], edges: list[dict]) -> list[str]:
    """Kolejność baz według przepływu danych: źródła przed odbiorcami (sortowanie topologiczne), potem alfabetycznie."""
    uses: dict[str, list[str]] = {name: [] for name in names}
    for e in edges:
        a, b = e["src"].split("|")[0], e["dst"].split("|")[0]
        if a != b and a in uses and b in uses and b not in uses[a]:
            uses[a].append(b)  # baza a korzysta z bazy b → b wcześniej

    order: list[str] = []

    def visit(name: str, path: set[str]) -> None:
        if name in order or name in path:
            return
        path.add(name)
        for dependency in uses[name]:
            visit(dependency, path)
        order.append(name)

    for name in sorted(names):
        visit(name, set())
    return order


def _domain_list(objects: list[dict], known_domains: dict, order: list[str]) -> list[dict]:
    """Domeny, w których są obiekty, z licznikami i kolorem (cyklicznie w obrębie bazy)."""
    counts: dict[str, dict] = {}
    for o in objects:
        c = counts.setdefault(o["domain"], {"count": 0, "tables": 0})
        c["count"] += 1
        if o["type"] == "Table":
            c["tables"] += 1

    result = []
    for domain_id, c in counts.items():
        domain = known_domains.get(domain_id) or _missing_auto_domain(domain_id)
        result.append(domain | c)

    position = {name: i for i, name in enumerate(order)}
    result.sort(key=lambda d: (position.get(d["project"], -1), d["sort"] or 0, (d["name"] or "").lower()))

    per_project: dict[str, int] = {}
    for d in result:
        index = per_project.get(d["project"], 0)
        d["color"] = d["color"] if d["color"] is not None else index % DOMAIN_COLORS + 1
        per_project[d["project"]] = index + 1

    return result


def _missing_auto_domain(domain_id: str) -> dict:
    """Domena automatyczna, której jeszcze nie ma w bazie (np. tuż po zmianie przypisań)."""
    parts = domain_id.split("|")
    return {
        "id": domain_id,
        "project": parts[1] if len(parts) > 1 else None,
        "name": parts[2] if len(parts) > 2 else domain_id,
        "description": None,
        "origin": "auto",
        "nameOrigin": "auto",
        "descOrigin": "auto",
        "sort": 1000,
        "color": None,
    }


def _project_entry(s: Session, ov: Overrides, project: Project, objects: list[dict]) -> dict:
    note = note_of(s, ov, "project:" + project.name)
    own = [o for o in objects if o["project"] == project.name]
    return {
        "name": project.name,
        "file": project.file,
        "format": project.format,
        "platform": project.platform,
        "description": note["description"],
        "descOrigin": note["origin"],
        "count": len(own),
        "tables": sum(1 for o in own if o["type"] == "Table"),
        "info": project.info or {},
    }


def _externals(s: Session, ov: Overrides, sid: int) -> list[dict]:
    result = []
    for x in s.scalars(select(External).where(External.snapshot_id == sid)):
        note = note_of(s, ov, x.id)
        result.append(
            {
                "snapshot_id": x.snapshot_id,
                "id": x.id,
                "name": note["name"] or x.name,
                "kind": x.kind,
                "description": note["description"],
                "descOrigin": note["origin"],
            }
        )
    return result
