"""Pozycje „do wyjaśnienia” (zakładka w UI, narzędzie atlas_issues): to, czego silnik nie rozstrzygnął.

Rodzaje: dynamic (dynamiczny SQL), unresolved (odwołanie do nieistniejącego obiektu),
external (system spoza solucji), parse (błąd składni pliku).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ssdt_atlas.architecture.overlay import issue_status, note_of
from ssdt_atlas.core.database import current_snapshot
from ssdt_atlas.core.models import DbObject, Issue, Proposal
from ssdt_atlas.core.project_files import Overrides
from ssdt_atlas.core.settings import TYPE_LETTER


def issues_view(s: Session, ov: Overrides) -> list[dict]:
    snapshot = current_snapshot(s)
    if not snapshot:
        return []

    proposals = {p.issue_key: p for p in s.scalars(select(Proposal))}
    objects = {o.id: o for o in s.scalars(select(DbObject).where(DbObject.snapshot_id == snapshot.id))}
    issues = s.scalars(select(Issue).where(Issue.snapshot_id == snapshot.id).order_by(Issue.kind, Issue.object_id))

    result = []
    for issue in issues:
        obj = objects.get(issue.object_id)
        proposal = proposals.get(issue.key)
        project, _, key = issue.object_id.partition("|")

        result.append(
            {
                "key": issue.key,
                "kind": issue.kind,
                "objectId": issue.object_id,
                "project": project,
                "object": f"{obj.schema_name}.{obj.name}" if obj else key,
                "t": TYPE_LETTER.get(obj.type) if obj else None,
                "ref": issue.ref,
                "extId": issue.ext_id,
                "line": issue.line,
                "snippet": issue.snippet,
                "message": issue.message,
                "proposal": {
                    "text": proposal.text,
                    "targets": proposal.targets or [],
                    "confidence": proposal.confidence,
                }
                if proposal
                else None,
                "extNote": note_of(s, ov, issue.ext_id) if issue.ext_id else None,
            }
            | issue_status(issue, ov)
        )

    return result
