"""REST API lokalnej aplikacji webowej (FastAPI) i pliki UI (katalog static/ komponentu).

Bezpieczeństwo (aplikacja nasłuchuje tylko na 127.0.0.1):
    - TrustedHostMiddleware odrzuca obce nagłówki Host (ochrona przed DNS rebinding),
    - zapis (POST) wymaga nagłówka X-Atlas: 1, którego strona z innej domeny nie może dodać bez CORS.

Odczyt: GET /api/state, /api/version, /api/model, /api/object, /api/issues, /api/changes, /api/guidelines.
Zapis:  POST /api/object, /api/domain, /api/domain/create, /api/note, /api/issue, /api/guidelines.

UI tylko pokazuje dane i zapisuje poprawki użytkownika. Nie uruchamia żadnych operacji (analizy, odświeżenia):
robi to wyłącznie Claude przez narzędzia MCP (/sqlproj-atlas:init, /sqlproj-atlas:refresh).
"""

import logging
from collections.abc import Iterator
from typing import Annotated

import git
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sqlproj_atlas.architecture import editing
from sqlproj_atlas.architecture.issues_view import issues_view
from sqlproj_atlas.architecture.map_view import map_model
from sqlproj_atlas.architecture.object_view import object_detail
from sqlproj_atlas.change_analysis.changes_view import changeset_view, find_changeset
from sqlproj_atlas.core.database import bump_revision, current_snapshot, revision, snapshot_info
from sqlproj_atlas.core.errors import AtlasError
from sqlproj_atlas.core.models import Issue
from sqlproj_atlas.core.settings import UI_DIR
from sqlproj_atlas.core.workspace import Workspace
from sqlproj_atlas.project_analysis.engine import engine_status

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Treść żądań zapisu. Pole obecne w żądaniu ustawia poprawkę, pusta wartość ją usuwa,
# pole pominięte zostaje bez zmian (dlatego korzystamy z model_fields_set).
# ---------------------------------------------------------------------------


class ObjectEdit(BaseModel):
    id: str
    description: str | None = None
    domain: str | None = None


class DomainEdit(BaseModel):
    id: str
    name: str | None = None
    description: str | None = None


class DomainCreate(BaseModel):
    project: str
    name: str
    description: str | None = None


class NoteEdit(BaseModel):
    target: str
    description: str | None = None
    name: str | None = None


class IssueDecision(BaseModel):
    key: str
    action: str  # accept, resolve, skip, reopen
    target: str | None = None
    text: str | None = None
    kind: str | None = None
    targetIsObject: bool = False  # noqa: N815 — nazwy pól jak w JSON z UI
    ext: bool = False


class Guidelines(BaseModel):
    markdown: str = ""


# ---------------------------------------------------------------------------
# Aplikacja
# ---------------------------------------------------------------------------


def create_app(ws: Workspace) -> FastAPI:
    app = FastAPI(title="SqlProj Atlas", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def no_cache(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AtlasError)
    async def atlas_error(_request: Request, error: AtlasError):
        return JSONResponse({"error": str(error)}, status_code=400)

    # --- zależności wspólne dla endpointów ---

    def session() -> Iterator[Session]:
        with ws.db.session() as s:
            yield s

    SessionDep = Annotated[Session, Depends(session)]

    def require_ui_header(x_atlas: Annotated[str | None, Header()] = None) -> None:
        if x_atlas != "1":
            raise HTTPException(status_code=403, detail="missing header")

    read = APIRouter(prefix="/api")
    write = APIRouter(prefix="/api", dependencies=[Depends(require_ui_header)])

    # --- odczyt ---

    @read.get("/ping")
    def ping():
        return {"ok": True, "project": str(ws.project_dir)}

    @read.get("/version")
    def version(s: SessionDep):
        return {"rev": revision(s)}

    @read.get("/state")
    def state(s: SessionDep):
        return _state(ws, s)

    @read.get("/model")
    def model(s: SessionDep):
        return map_model(s, ws.files.overrides())

    @read.get("/object")
    def get_object(id: str, s: SessionDep):  # noqa: A002
        return object_detail(s, ws.files.overrides(), id) or {"error": "not found"}

    @read.get("/issues")
    def issues(s: SessionDep):
        return issues_view(s, ws.files.overrides())

    @read.get("/changes")
    def changes(s: SessionDep, id: int | None = None):  # noqa: A002
        return changeset_view(s, ws.files.overrides(), id)

    @read.get("/guidelines")
    def get_guidelines():
        return {"markdown": ws.files.guidelines() or "", "file": str(ws.files.guidelines_file)}

    # --- zapis: poprawki użytkownika (overrides.json) ---

    @write.post("/object")
    def edit_object(body: ObjectEdit, s: SessionDep):
        _apply(ws, "objects", body.id, body, ("description", "domain"))
        bump_revision(s)
        return {"ok": True}

    @write.post("/domain")
    def edit_domain(body: DomainEdit, s: SessionDep):
        _apply(ws, "domains", body.id, body, ("name", "description"))
        bump_revision(s)
        return {"ok": True}

    @write.post("/domain/create")
    def create_domain(body: DomainCreate, s: SessionDep):
        return {"id": editing.create_user_domain(s, ws.files, body.project, body.name, body.description)}

    @write.post("/note")
    def edit_note(body: NoteEdit, s: SessionDep):
        _apply(ws, "notes", body.target, body, ("description", "name"))
        bump_revision(s)
        return {"ok": True}

    @write.post("/issue")
    def decide_issue(body: IssueDecision, s: SessionDep):
        snapshot = current_snapshot(s)
        issue = s.get(Issue, (snapshot.id, body.key)) if snapshot else None
        if not issue:
            raise AtlasError("Nieznana pozycja")

        editing.resolve_issue(s, ws.files, issue, body.action, target=body.target, text=body.text, kind=body.kind,
                              target_is_object=body.targetIsObject, describes_external=body.ext)  # fmt: skip
        return {"ok": True}

    @write.post("/guidelines")
    def save_guidelines(body: Guidelines, s: SessionDep):
        ws.files.save_guidelines(body.markdown)
        bump_revision(s)
        return {"ok": True}

    app.include_router(read)
    app.include_router(write)

    # pliki UI (index.html, app.js, app.css) pod „/”; montowane na końcu, po trasach /api
    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
    return app


# ---------------------------------------------------------------------------
# Pomocnicze
# ---------------------------------------------------------------------------


def _state(ws: Workspace, s: Session) -> dict:
    """Nagłówek UI: projekt, ostatnia analiza, liczba nowych commitów, ostatni zestaw zmian."""
    snapshot = current_snapshot(s)
    changeset = find_changeset(s)
    model = map_model(s, ws.files.overrides()) if snapshot else None

    new_commits = 0
    if snapshot and snapshot.commit_sha and ws.git.is_repo:
        try:
            new_commits = len(ws.git.log(snapshot.commit_sha))
        except git.GitCommandError:
            log.warning("Commit ostatniej analizy nie istnieje w historii git (np. po rebase)")

    changed = None
    if changeset:
        stats = changeset.stats or {}
        changed = sum(stats.get(k) or 0 for k in ("added", "modified", "removed", "renamed"))

    return {
        "project": ws.project_dir.name,
        "projectDir": str(ws.project_dir),
        "rev": revision(s),
        "initialized": snapshot is not None,
        "snapshot": snapshot_info(snapshot),
        "guidelines": bool(ws.files.guidelines()),
        "engine": engine_status()["ok"],
        "newCommits": new_commits,
        "changeset": {"id": changeset.id, "changed": changed} if changeset else None,
        "openIssues": model["openIssues"] if model else 0,
        "objects": len(model["objects"]) if model else 0,
    }


def _apply(ws: Workspace, section: str, key: str, body: BaseModel, fields: tuple[str, ...]) -> None:
    """Zapisuje do overrides.json pola obecne w żądaniu (puste usuwają poprawkę)."""
    for field in fields:
        if field in body.model_fields_set:
            ws.files.set_override(section, key, field, getattr(body, field))
