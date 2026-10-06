"""Zapis snapshotu: wynik silnika → obiekty, relacje, pozycje „do wyjaśnienia” i systemy zewnętrzne w bazie.

Tu zapadają decyzje interpretacyjne:
    - odwołanie do innej bazy z solucji staje się zwykłą relacją między projektami,
    - odwołanie do bazy spoza solucji albo linked servera staje się systemem zewnętrznym „ext|nazwa”
      i pozycją „do wyjaśnienia” (kind=external),
    - odwołanie do nieistniejącego obiektu w innej bazie solucji to pozycja kind=unresolved.
"""

from dataclasses import dataclass, field

from sqlalchemy import insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from data_architect.core.database import bump_revision
from data_architect.core.models import (
    DbObject,
    Definition,
    Edge,
    External,
    Issue,
    Project,
    Snapshot,
)
from data_architect.core.project_files import now_iso
from data_architect.core.settings import TYPE_LETTER
from data_architect.project_analysis.engine import EngineProject
from data_architect.project_analysis.references import resolve_database
from data_architect.project_analysis.sqlproj import SqlProject


@dataclass
class SnapshotMeta:
    """Skąd pochodzi snapshot: commit HEAD i czy były niezatwierdzone zmiany."""

    commit_sha: str | None
    commit_date: str
    author: str | None
    message: str | None
    dirty: bool = False
    label: str | None = None


@dataclass
class _Collected:
    """Wiersze snapshotu zebrane przed zapisem do bazy."""

    objects: list[dict] = field(default_factory=list)
    definitions: dict[str, str] = field(default_factory=dict)
    edges: dict[tuple[str, str, str], None] = field(default_factory=dict)  # zbiór z zachowaniem kolejności
    issues: dict[str, dict] = field(default_factory=dict)
    externals: dict[str, dict] = field(default_factory=dict)

    def add_edge(self, src: str, dst: str, kind: str) -> None:
        self.edges[(src, dst, kind)] = None

    def add_issue(self, object_id: str, kind: str, ref: str, message: str | None,
                  ext_id: str | None = None, line: int = 0, snippet: str | None = None) -> None:  # fmt: skip
        key = f"{object_id}|{kind}|{ref}"
        self.issues.setdefault(
            key,
            {"key": key, "object_id": object_id, "kind": kind, "ref": ref, "message": message,
             "ext_id": ext_id, "line": line or 0, "snippet": snippet},
        )  # fmt: skip

    def add_external(self, name: str, kind: str) -> str:
        ext_id = "ext|" + name.lower()
        self.externals.setdefault(ext_id, {"id": ext_id, "name": name, "kind": kind})
        return ext_id


# ---------------------------------------------------------------------------
# Zapis snapshotu
# ---------------------------------------------------------------------------


def save_snapshot(
    s: Session, meta: SnapshotMeta, projects: list[SqlProject], engine_projects: list[EngineProject]
) -> int:
    """Zapisuje snapshot w jednej transakcji i zwraca jego id."""
    rows = _collect(projects, engine_projects)

    snapshot = Snapshot(
        commit_sha=meta.commit_sha,
        commit_date=meta.commit_date,
        author=meta.author,
        message=meta.message,
        kind="analysis",
        dirty=int(meta.dirty),
        created_at=now_iso(),
        stats=_stats(rows),
        label=meta.label,
    )
    s.add(snapshot)
    s.flush()  # nadaje snapshot.id
    sid = snapshot.id

    # --- projekty (bazy) ---
    by_name = {p.name: p for p in engine_projects}
    for p in projects:
        engine = by_name.get(p.name)
        info = p.info() | {"messages": (engine.messages if engine else [])[:50]}
        s.add(
            Project(
                snapshot_id=sid,
                name=p.name,
                file=p.file,
                format=p.format,
                platform=engine.platform if engine else None,
                info=info,
            )
        )

    # --- definicje (współdzielone między snapshotami) i obiekty ---
    if rows.definitions:
        s.execute(
            sqlite_insert(Definition).on_conflict_do_nothing(),
            [{"hash": h, "text": t} for h, t in rows.definitions.items()],
        )
    if rows.objects:
        s.execute(insert(DbObject), [o | {"snapshot_id": sid} for o in rows.objects])

    # --- relacje, pozycje do wyjaśnienia, systemy zewnętrzne ---
    if rows.edges:
        s.execute(
            insert(Edge),
            [{"snapshot_id": sid, "src": a, "dst": b, "kind": k, "origin": "engine"} for a, b, k in rows.edges],
        )
    if rows.issues:
        s.execute(insert(Issue), [i | {"snapshot_id": sid} for i in rows.issues.values()])
    if rows.externals:
        s.execute(insert(External), [x | {"snapshot_id": sid} for x in rows.externals.values()])

    bump_revision(s)
    return sid


def _collect(projects: list[SqlProject], engine_projects: list[EngineProject]) -> _Collected:
    """Przekłada wynik silnika na wiersze bazy i rozwiązuje odwołania między bazami."""
    rows = _Collected()
    project_by_name = {p.name.lower(): p for p in projects}
    object_ids = {f"{ep.name}|{o.key}" for ep in engine_projects for o in ep.objects}

    for ep in engine_projects:
        project = project_by_name.get(ep.name.lower())

        # --- obiekty ---
        for o in ep.objects:
            rows.definitions.setdefault(o.hash, o.definition or "")
            rows.objects.append(
                {
                    "id": f"{ep.name}|{o.key}",
                    "project": ep.name,
                    "schema_name": o.schema_name,
                    "name": o.name,
                    "type": o.type,
                    "file": o.file,
                    "line": o.line,
                    "hash": o.hash,
                    "columns": [c.model_dump() for c in o.columns] if o.columns else None,
                    "params": [p.model_dump() for p in o.params] if o.params else None,
                }
            )

        # --- relacje ---
        for e in ep.edges:
            src = f"{ep.name}|{e.source}"
            if not e.external_db:
                rows.add_edge(src, f"{ep.name}|{e.to}", e.kind)
                continue

            target = resolve_database(project, e.external_db, projects)
            if target.get("system"):
                continue

            if target.get("project"):
                dst = f"{target['project']}|{e.to}"
                if dst in object_ids:
                    rows.add_edge(src, dst, e.kind)
                else:
                    rows.add_issue(
                        src,
                        "unresolved",
                        f"{target['project']}.{e.to}",
                        f"Obiekt nie istnieje w bazie {target['project']}",
                    )
                continue

            # system zewnętrzny: linked server albo baza spoza solucji
            if e.external_server:
                ext_id = rows.add_external(e.external_server, "linked-server")
            else:
                ext_id = rows.add_external(target["ext"], target["kind"])
            rows.add_edge(src, ext_id, e.kind)

            if target["kind"] == "variable":
                message = f"Zmienna SQLCMD {e.external_db} nie wskazuje projektu w solucji ({e.to})"
            else:
                message = f"Odwołanie do bazy spoza solucji: {e.external_db} ({e.to})"
            rows.add_issue(src, "external", rows.externals[ext_id]["name"], message, ext_id=ext_id)

        # --- problemy zgłoszone przez silnik ---
        for i in ep.issues:
            object_id = f"{ep.name}|{i.object}"
            if i.kind == "openquery":
                ext_id = rows.add_external(i.server or i.ref, "linked-server")
                rows.add_edge(object_id, ext_id, "reads")
                message = f"OPENQUERY do serwera {i.server}: {i.message or ''}".strip()
                rows.add_issue(
                    object_id,
                    "external",
                    rows.externals[ext_id]["name"],
                    message,
                    ext_id=ext_id,
                    line=i.line,
                    snippet=i.snippet,
                )
            else:
                rows.add_issue(object_id, i.kind, i.ref, i.message, line=i.line, snippet=i.snippet)

        # --- błędy składni (plik, którego nie da się sparsować) ---
        for pe in ep.parse_errors:
            rows.add_issue(f"{ep.name}|file:{pe.file}", "parse", pe.file, pe.message, line=pe.line)

    return rows


def _stats(rows: _Collected) -> dict:
    """Liczniki snapshotu: łącznie i dla każdego projektu według typów."""
    stats = {"objects": len(rows.objects), "edges": len(rows.edges), "issues": len(rows.issues), "projects": {}}
    for o in rows.objects:
        project = stats["projects"].setdefault(o["project"], {"total": 0, "tables": 0, "code": 0, "byType": {}})
        project["total"] += 1
        project["tables" if o["type"] == "Table" else "code"] += 1
        letter = TYPE_LETTER.get(o["type"], o["type"])
        project["byType"][letter] = project["byType"].get(letter, 0) + 1
    return stats
