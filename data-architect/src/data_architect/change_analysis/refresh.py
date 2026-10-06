"""Odświeżenie: nowa analiza projektu i zestaw zmian względem poprzedniej analizy."""

from data_architect.change_analysis.changeset import create_changeset
from data_architect.core.database import current_snapshot
from data_architect.core.workspace import Workspace
from data_architect.project_analysis.pipeline import analyze_unlocked


def refresh(ws: Workspace) -> dict:
    """Analiza i zestaw zmian: {snapshot, previous, changeset}.

    Gdy projekt nie był jeszcze analizowany, wykonuje tylko analizę (changeset = None).
    """
    with ws.analysis_lock:
        # 1. poprzednia analiza (punkt odniesienia)
        with ws.db.session() as s:
            previous = current_snapshot(s)
            previous_id = previous.id if previous else None

        # 2. nowa analiza
        snapshot_id = analyze_unlocked(ws)
        if previous_id is None:
            return {"snapshot": snapshot_id, "previous": None, "changeset": None}

        # 3. porównanie i lista commitów
        with ws.db.session() as s:
            changeset_id = create_changeset(s, ws.git, ws.files, previous_id, snapshot_id)

        return {"snapshot": snapshot_id, "previous": previous_id, "changeset": changeset_id}
