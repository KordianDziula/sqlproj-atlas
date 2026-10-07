"""Domeny automatyczne: zanim Claude zagreguje architekturę, każdy obiekt należy do domeny swojego schematu.

Id domeny automatycznej: „auto|Projekt|schemat”. Wywoływane po każdej analizie projektu
(project_analysis/pipeline.py), więc nowe obiekty od razu mają miejsce na mapie.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from sqlproj_atlas.core.models import DbObject, Domain, Semantic
from sqlproj_atlas.core.project_files import now_iso


def auto_domain_id(project: str, schema: str) -> str:
    return f"auto|{project}|{schema.lower()}"


def ensure_auto_domains(s: Session, snapshot_id: int) -> None:
    """Obiekt bez domeny (albo z domeną, która już nie istnieje) dostaje domenę automatyczną według schematu."""
    known_domains = set(s.scalars(select(Domain.id)))
    semantics = {sem.object_id: sem for sem in s.scalars(select(Semantic))}

    for obj in s.scalars(select(DbObject).where(DbObject.snapshot_id == snapshot_id)):
        sem = semantics.get(obj.id)
        if sem and sem.domain_id in known_domains:
            continue

        # --- domena schematu (tworzona przy pierwszym obiekcie) ---
        domain_id = auto_domain_id(obj.project, obj.schema_name)
        if domain_id not in known_domains:
            s.add(
                Domain(
                    id=domain_id,
                    project=obj.project,
                    name=obj.schema_name,
                    description=f"Obiekty schematu {obj.schema_name}",
                    origin="auto",
                    sort=1000,
                )
            )
            known_domains.add(domain_id)

        # --- przypisanie obiektu ---
        if sem:
            sem.domain_id = domain_id
        else:
            sem = Semantic(object_id=obj.id, domain_id=domain_id, origin="auto", updated_at=now_iso())
            s.add(sem)
            semantics[obj.id] = sem
