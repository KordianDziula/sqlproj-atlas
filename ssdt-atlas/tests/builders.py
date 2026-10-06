"""Dane syntetyczne do testów bez silnika: snapshoty z obiektami, relacjami i problemami zapisane wprost w bazie.

    sid = add_snapshot(s, [table("Sales|sales.orders", columns=[col("OrderId", pk=True)])])

Id obiektu ma postać „Projekt|schemat.nazwa”; projekt, schemat i nazwa są z niego wyliczane.
"""

from sqlalchemy.orm import Session

from ssdt_atlas.core.models import DbObject, Edge, Issue, Project, Snapshot


def col(name: str, type: str = "int", nullable: bool = False, pk: bool = False) -> dict:  # noqa: A002
    return {"name": name, "type": type, "nullable": nullable, "pk": pk}


def param(name: str, type: str = "int", output: bool = False) -> dict:  # noqa: A002
    return {"name": name, "type": type, "output": output}


def obj(object_id: str, type: str = "Table", hash: str | None = None, **fields) -> dict:  # noqa: A002
    """Obiekt do add_snapshot; domyślny hash zależy od id (różne obiekty mają różne definicje)."""
    project, key = object_id.split("|", 1)
    schema, name = key.split(".", 1)
    return {
        "id": object_id,
        "project": project,
        "schema_name": schema,
        "name": name,
        "type": type,
        "file": f"{schema}/{name}.sql",
        "line": 1,
        "hash": hash or f"hash-{object_id}",
        "columns": None,
        "params": None,
    } | fields


def table(object_id: str, **fields) -> dict:
    return obj(object_id, "Table", **fields)


def procedure(object_id: str, **fields) -> dict:
    return obj(object_id, "Procedure", **fields)


def edge(src: str, dst: str, kind: str = "reads") -> dict:
    return {"src": src, "dst": dst, "kind": kind}


def issue(object_id: str, kind: str, ref: str, message: str = "", ext_id: str | None = None) -> dict:
    return {
        "key": f"{object_id}|{kind}|{ref}",
        "object_id": object_id,
        "kind": kind,
        "ref": ref,
        "message": message,
        "ext_id": ext_id,
    }


def add_snapshot(s: Session, objects: list[dict], edges: list[dict] = (), issues: list[dict] = (),
                 renames: dict[str, list[dict]] | None = None, commit_sha: str | None = None,
                 dirty: bool = False) -> int:  # fmt: skip
    """Zapisuje snapshot i zwraca jego id. `renames`: projekt → wpisy .refactorlog (element, newName)."""
    snapshot = Snapshot(commit_sha=commit_sha, commit_date="2026-10-01", kind="analysis", dirty=int(dirty),
                        created_at="2026-10-01T00:00:00.000Z", stats={})  # fmt: skip
    s.add(snapshot)
    s.flush()

    projects = {o["project"] for o in objects} | set(renames or {})
    for name in sorted(projects):
        s.add(Project(snapshot_id=snapshot.id, name=name, file=f"{name}/{name}.sqlproj", format="sdk",
                      info={"renames": (renames or {}).get(name, [])}))  # fmt: skip

    for o in objects:
        s.add(DbObject(snapshot_id=snapshot.id, **o))
    for e in edges:
        s.add(Edge(snapshot_id=snapshot.id, origin="engine", **e))
    for i in issues:
        s.add(Issue(snapshot_id=snapshot.id, line=0, **i))

    s.flush()
    return snapshot.id
