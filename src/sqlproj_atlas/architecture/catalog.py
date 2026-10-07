"""Katalog obiektów dla Claude'a (atlas_list_objects): zwięzła lista do agregacji architektury partiami."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from sqlproj_atlas.architecture.map_view import map_model
from sqlproj_atlas.core.models import DbObject, Semantic
from sqlproj_atlas.core.project_files import Overrides


def list_objects(s: Session, ov: Overrides, filters: dict, offset: int, limit: int) -> dict:
    """Partia obiektów z kolumnami, zależnościami, domeną i opisem; puste pola są pomijane."""
    model = map_model(s, ov)
    objects = _filter(model["objects"], filters)
    domain_names = {d["id"]: d["name"] for d in model["domains"]}

    # relacje: wychodzące (z czego obiekt korzysta) i liczba przychodzących (kto z niego korzysta)
    outgoing: dict[str, list[dict]] = {}
    used_by: dict[str, int] = {}
    for e in model["edges"]:
        outgoing.setdefault(e["src"], []).append(e)
        used_by[e["dst"]] = used_by.get(e["dst"], 0) + 1

    rows = {o.id: o for o in s.scalars(select(DbObject).where(DbObject.snapshot_id == model["snapshot"]["id"]))}
    descriptions = dict(s.execute(select(Semantic.object_id, Semantic.description)).all())

    items = []
    for o in objects[offset : offset + limit]:
        row = rows[o["id"]]
        user = ov.objects.get(o["id"])
        locked = [field for field in ("domain", "description") if user and getattr(user, field)]
        user_description = user.description if user and user.description else None

        item = {
            "id": o["id"],
            "t": o["t"],
            "domain": o["domain"],
            "domainName": domain_names.get(o["domain"]),
            "columns": _columns(row),
            "params": ", ".join(p["name"] for p in row.params) if row.params else None,
            "uses": [f"{_display_name(e['dst'])} ({e['kind']})" for e in outgoing.get(o["id"], [])[:12]],
            "usedByCount": used_by.get(o["id"], 0),
            "description": user_description or descriptions.get(o["id"]),
            "userLocked": locked or None,  # pola poprawione przez użytkownika (Claude ich nie zmieni)
            "openIssues": o["warn"] or None,
        }
        items.append({k: v for k, v in item.items() if v is not None})

    total = len(objects)
    hint = f"Kolejna partia: offset={offset + limit}" if offset + limit < total else "To ostatnia partia."
    return {"total": total, "offset": offset, "limit": limit, "items": items, "hint": hint}


def _columns(row: DbObject) -> str | None:
    """Do 14 kolumn tabeli, klucz główny oznaczony gwiazdką: „OrderId*, CustomerId, …”."""
    if not row.columns:
        return None
    return ", ".join(c["name"] + ("*" if c.get("pk") else "") for c in row.columns[:14])


def _filter(objects: list[dict], f: dict) -> list[dict]:
    def same(a: str, b: str) -> bool:
        return a.lower() == b.lower()

    if f.get("project"):
        objects = [o for o in objects if same(o["project"], f["project"])]
    if f.get("schema"):
        objects = [o for o in objects if same(o["schema"], f["schema"])]
    if f.get("domain"):
        objects = [o for o in objects if o["domain"] == f["domain"]]
    if f.get("type"):
        objects = [o for o in objects if o["t"] == f["type"]]
    if f.get("unassigned"):
        objects = [o for o in objects if o["domain"].startswith("auto|")]
    if f.get("undescribed"):
        objects = [o for o in objects if not o["described"]]
    if f.get("search"):
        objects = [o for o in objects if f["search"].lower() in f"{o['schema']}.{o['name']}".lower()]
    return objects


def _display_name(object_id: str) -> str:
    """„Sales|sales.orders” → „Sales.sales.orders”; system zewnętrzny „ext|crm” → „crm (zewn.)”."""
    if object_id.startswith("ext|"):
        return object_id[4:] + " (zewn.)"
    return object_id.replace("|", ".", 1)
