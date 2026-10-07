"""Rozwiązywanie odwołań między bazami.

Kod SQL odwołuje się do innych baz na dwa sposoby:
    [$(Staging)].stg.Orders   zmienna SQLCMD; jej znaczenie wynika z referencji w .sqlproj
    [Staging].stg.Orders      nazwa wprost (literał)

Wynik to jeden z trzech przypadków:
    {"project": "Staging"}                 baza z tej samej solucji
    {"ext": "ErpDb", "kind": "database"}   system zewnętrzny (database, dacpac, variable)
    {"system": True}                       baza systemowa SQL Server (master, msdb, ...), pomijana
"""

import re

from sqlproj_atlas.project_analysis.sqlproj import ProjectReference, SqlProject

SYSTEM_DATABASES = {"master", "msdb", "model", "tempdb"}


def resolve_database(project: SqlProject | None, token: str, projects: list[SqlProject]) -> dict:
    """Rozwiązuje nazwę bazy `token` użytą w projekcie `project`."""
    references = project.references if project else []

    # --- zmienna SQLCMD: $(Nazwa) ---
    variable = re.fullmatch(r"\$\((.+)\)", token)
    if variable:
        name = variable.group(1)
        ref = next((r for r in references if (r.variable or "").lower() == name.lower()), None)

        if ref and ref.kind == "project":
            target = _find_project(ref, projects)
            return {"project": target.name} if target else {"ext": ref.name, "kind": "database"}

        if ref and ref.kind == "dacpac":
            return {"system": True} if ref.name.lower() in SYSTEM_DATABASES else {"ext": ref.name, "kind": "dacpac"}

        return {"ext": name, "kind": "variable"}

    # --- nazwa wprost ---
    lowered = token.lower()
    if lowered in SYSTEM_DATABASES:
        return {"system": True}

    same_name = next((p for p in projects if p.name.lower() == lowered), None)
    if same_name:
        return {"project": same_name.name}

    ref = next((r for r in references if (r.literal or "").lower() == lowered), None)
    if ref and ref.kind == "project":
        target = _find_project(ref, projects)
        if target:
            return {"project": target.name}
    if ref and ref.kind == "dacpac" and ref.name.lower() in SYSTEM_DATABASES:
        return {"system": True}

    return {"ext": token, "kind": "database"}


def _find_project(ref: ProjectReference, projects: list[SqlProject]) -> SqlProject | None:
    """Projekt wskazany referencją: po ścieżce pliku, a gdy się nie zgadza, po nazwie."""
    by_path = next((p for p in projects if p.file == ref.path), None)
    return by_path or next((p for p in projects if p.name.lower() == (ref.name or "").lower()), None)
