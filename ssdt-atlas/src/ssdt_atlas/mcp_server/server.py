"""Serwer MCP (oficjalny SDK `mcp`): narzędzia, którymi Claude analizuje projekt i agreguje architekturę.

Każde narzędzie to funkcja z typami i docstringiem; SDK buduje z nich schemat i opis widoczny dla Claude'a.
Narzędzia są cienkie: otwierają sesję bazy i wołają warstwy analysis / semantics / views.

Podział ról:
    silnik (atlas_analyze, atlas_refresh)      fakty: obiekty, relacje, problemy
    Claude (atlas_save_domains, atlas_assign…) domeny, opisy, wyjaśnienia
    użytkownik (UI, overrides.json)            ostatnie słowo; Claude dostaje informację o jego poprawkach
"""

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field
from sqlalchemy.orm import Session

from ssdt_atlas import __version__
from ssdt_atlas.architecture import editing
from ssdt_atlas.architecture.catalog import list_objects
from ssdt_atlas.architecture.editing import AssignItem, ChangeNote, DescribeItem, DomainInput, ProposalInput
from ssdt_atlas.architecture.issues_view import issues_view
from ssdt_atlas.architecture.lookup import NO_ANALYSIS, require_analysis, resolve_object_id
from ssdt_atlas.architecture.map_view import map_model
from ssdt_atlas.architecture.object_view import impact, object_detail
from ssdt_atlas.change_analysis.changes_view import changeset_for_claude, find_changeset
from ssdt_atlas.change_analysis.refresh import refresh
from ssdt_atlas.core.database import bump_revision, current_snapshot
from ssdt_atlas.core.errors import AtlasError
from ssdt_atlas.core.workspace import Workspace
from ssdt_atlas.mcp_server.status import project_status
from ssdt_atlas.project_analysis.engine import engine_status
from ssdt_atlas.project_analysis.pipeline import analyze
from ssdt_atlas.project_analysis.summary import analysis_summary
from ssdt_atlas.web_app.runner import close_ui, open_ui

INSTRUCTIONS = """SSDT Atlas analizuje projekty bazodanowe SSDT (SQL Server) w bieżącym katalogu.
Silnik Microsoft DacFx + ScriptDom dostarcza fakty (obiekty, kolumny, relacje, nierozwiązane odwołania).
Ty agregujesz architekturę: domeny biznesowe, opisy, wyjaśnienia; użytkownik może wszystko poprawić w UI i jego poprawki
mają pierwszeństwo.
Zacznij od atlas_status. Przepływy: /ssdt-atlas:setup (zależności), /ssdt-atlas:init (pierwsze uruchomienie),
/ssdt-atlas:refresh (zmiany od ostatniej analizy), /ssdt-atlas:open (UI), /ssdt-atlas:close (zamknięcie UI)."""

mcp = MCPServer(name="ssdt-atlas", version=__version__, instructions=INSTRUCTIONS, log_level="WARNING")

# projekt, na którym działają narzędzia; ustawiany raz przy starcie procesu
_workspace: Workspace | None = None


def run(workspace: Workspace) -> None:
    """Uruchamia serwer MCP po stdio (blokuje do zamknięcia strumienia przez Claude Code)."""
    global _workspace
    _workspace = workspace
    mcp.run("stdio")


def tool(fn: Callable) -> Callable:
    """Rejestruje narzędzie. AtlasError trafia do Claude'a jako czytelny komunikat (ToolError);
    inne wyjątki SDK zamienia na ogólny błąd i zapisuje w logu."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AtlasError as e:
            raise ToolError(str(e)) from e

    return mcp.tool(structured_output=False)(wrapper)


NO_PREVIOUS_ANALYSIS = "Brak poprzedniej analizy. Uruchom najpierw /ssdt-atlas:init."
NO_CHANGES = "Brak zapisanych zmian. Uruchom atlas_refresh."
NO_CHANGESET = "Brak zestawu zmian"


@contextmanager
def _analyzed_session(missing: str = NO_ANALYSIS) -> Iterator[Session]:
    """Sesja bazy dla narzędzi, które wymagają wcześniejszej analizy. Bez bazy od razu czytelny błąd,
    bez zakładania plików w projekcie (np. gdy Claude zapyta o obiekt w projekcie, który nie jest SSDT)."""
    if not ws().has_data:
        raise AtlasError(missing)
    with ws().db.session() as s:
        yield s


def ws() -> Workspace:
    assert _workspace is not None, "Serwer nie został uruchomiony przez run()"
    return _workspace


# ---------------------------------------------------------------------------
# Stan i analiza
# ---------------------------------------------------------------------------


@tool
def atlas_status() -> dict:
    """Stan SSDT Atlas w bieżącym projekcie: czy wykonano analizę, ile nowych commitów od ostatniej analizy,
    czy są wskazówki, stan silnika, otwarte pozycje do wyjaśnienia, adres UI. Wywołaj jako pierwsze."""
    return project_status(ws())


@tool
def atlas_analyze(label: Annotated[str | None, Field(description="Opcjonalna etykieta snapshotu")] = None) -> dict:
    """Pełna analiza projektów SSDT z katalogu roboczego silnikiem Microsoft DacFx + ScriptDom. Tworzy snapshot
    i zwraca podsumowanie (projekty, schematy, konwencje nazw, zmienne SQLCMD, systemy zewnętrzne, problemy)
    przydatne do wywiadu z użytkownikiem. Trwa od kilku sekund do kilku minut."""
    _require_engine()
    snapshot_id = analyze(ws(), label)

    with ws().db.session() as s:
        return analysis_summary(s, ws().files.overrides(), snapshot_id, ws().files.config())


@tool
def atlas_configure(
    excludeProjects: Annotated[list[str] | None, Field(description="Nazwy projektów .sqlproj do pominięcia")] = None,  # noqa: N803
    excludeSchemas: Annotated[  # noqa: N803
        list[str] | None,
        Field(description="Schematy do pominięcia: „schemat” we wszystkich bazach albo „Projekt.schemat” w jednej"),
    ] = None,
) -> dict:
    """Ustawienia analizy zapisywane w .claude/ssdt-atlas/config.json: projekty do pominięcia (testowe, przykładowe)
    i schematy do pominięcia (np. tymczasowe, techniczne, kopie). Po zmianie uruchom ponownie atlas_analyze."""
    config = ws().files.config()
    if excludeProjects is not None:
        config["excludeProjects"] = excludeProjects
    if excludeSchemas is not None:
        config["excludeSchemas"] = excludeSchemas
    ws().files.save_config(config)
    return {"saved": config}


@tool
def atlas_guidelines(
    action: Annotated[Literal["get", "save"], Field(description="get albo save")],
    markdown: Annotated[str | None, Field(description="Pełna treść wskazówek (dla save)")] = None,
) -> dict:
    """Odczyt lub zapis wskazówek interpretacji projektu (guidelines.md, w repozytorium).
    Format: sekcje Markdown „## Tytuł” z treścią. Zapis nadpisuje cały plik."""
    files = ws().files
    if action == "get":
        return {"file": str(files.guidelines_file), "markdown": files.guidelines()}

    if not markdown:
        raise AtlasError("Brak treści markdown")
    files.save_guidelines(markdown)
    _bump()
    return {"saved": str(files.guidelines_file)}


# ---------------------------------------------------------------------------
# Odczyt obiektów
# ---------------------------------------------------------------------------


@tool
def atlas_list_objects(
    project: Annotated[str | None, Field(description="Nazwa projektu (bazy)")] = None,
    schema: Annotated[str | None, Field(description="Schemat")] = None,
    domain: Annotated[str | None, Field(description="Id domeny")] = None,
    type: Annotated[str | None, Field(description="Typ: T, V, P, F, Tr, Sy, Sq, TT")] = None,  # noqa: A002
    unassigned: Annotated[
        bool, Field(description="Tylko obiekty w domenach automatycznych (bez domeny od Claude'a lub użytkownika)")
    ] = False,
    undescribed: Annotated[bool, Field(description="Tylko obiekty bez opisu")] = False,
    search: Annotated[str | None, Field(description="Fragment nazwy")] = None,
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=200)] = 60,
) -> dict:
    """Lista obiektów z ostatniej analizy w zwięzłej formie (typ, kolumny, z czego korzysta, ile obiektów z niego
    korzysta, domena, opis). Służy do agregacji architektury partiami. Filtry są opcjonalne."""
    filters = {"project": project, "schema": schema, "domain": domain, "type": type,
               "unassigned": unassigned, "undescribed": undescribed, "search": search}  # fmt: skip

    with _analyzed_session() as s:
        require_analysis(s)
        return list_objects(s, ws().files.overrides(), filters, offset, limit)


@tool
def atlas_get_object(
    id: Annotated[str, Field(description="Id obiektu: „Projekt|schemat.nazwa” albo „schemat.nazwa”")],
) -> dict:  # noqa: A002
    """Szczegóły obiektu: kolumny, parametry, definicja SQL, relacje w obie strony, problemy, notatki o zmianach."""
    with _analyzed_session() as s:
        detail = object_detail(s, ws().files.overrides(), resolve_object_id(s, id))

    # długie definicje skracamy, żeby nie zapychać kontekstu Claude'a
    if detail["definition"] and len(detail["definition"]) > 6000:
        detail["definition"] = detail["definition"][:6000] + "\n…(skrócono)"
    return detail


@tool
def atlas_impact(
    id: Annotated[str, Field(description="Id obiektu")],  # noqa: A002
    depth: Annotated[int, Field(ge=1, le=10)] = 5,
) -> dict:
    """Analiza wpływu: wszystkie obiekty zależne (przechodnio) od wskazanego obiektu."""
    with _analyzed_session() as s:
        object_id = resolve_object_id(s, id)
        items = impact(s, ws().files.overrides(), object_id, depth)
    return {"object": object_id, "dependents": len(items), "items": items}


@tool
def atlas_issues(
    status: Annotated[Literal["open", "done", "skip", "all"], Field(description="Status pozycji")] = "open",
    kind: Annotated[str | None, Field(description="dynamic, unresolved, external, parse")] = None,
) -> dict:
    """Pozycje „Do wyjaśnienia”: dynamiczny SQL, nierozwiązane odwołania, systemy zewnętrzne, błędy składni. Domyślnie
    otwarte.
    """
    with _analyzed_session() as s:
        require_analysis(s)
        items = issues_view(s, ws().files.overrides())

    items = [i for i in items if (status == "all" or i["status"] == status) and (not kind or i["kind"] == kind)]
    return {"total": len(items), "items": items[:100]}


# ---------------------------------------------------------------------------
# Agregacja architektury przez Claude'a
# ---------------------------------------------------------------------------


@tool
def atlas_save_domains(domains: list[DomainInput]) -> dict:
    """Tworzy lub aktualizuje domeny biznesowe (agregacja architektury przez Claude'a). Każda domena należy do
    jednego projektu. Zwraca id domen do użycia w atlas_assign. Nie nadpisuje nazw i opisów poprawionych przez
    użytkownika."""
    with _analyzed_session() as s:
        return {"domains": editing.save_domains(s, domains)}


@tool
def atlas_assign(items: list[AssignItem]) -> dict:
    """Przypisuje obiekty do domen i opcjonalnie nadaje im opisy (1–2 zdania z perspektywy biznesu).
    Przypisania i opisy poprawione przez użytkownika mają pierwszeństwo i nie są nadpisywane."""
    with _analyzed_session() as s:
        result = editing.assign(s, ws().files, items)

    # ile obiektów wciąż czeka na domenę (Claude sprawdza to po każdej bazie)
    with _analyzed_session() as s:
        waiting = [o for o in map_model(s, ws().files.overrides())["objects"] if o["domain"].startswith("auto|")]

    by_project: dict[str, int] = {}
    for o in waiting:
        by_project[o["project"]] = by_project.get(o["project"], 0) + 1
    return result | {"objectsStillInAutoDomains": len(waiting), "stillInAutoDomainsByProject": by_project}


@tool
def atlas_describe(items: list[DescribeItem]) -> dict:
    """Opisy baz, systemów zewnętrznych i domen. target: „project:Nazwa”, „ext|nazwa” (id systemu zewnętrznego) albo
    „domain:<id>”.
    """
    with _analyzed_session() as s:
        return editing.describe(s, items)


@tool
def atlas_propose(items: list[ProposalInput]) -> dict:
    """Propozycje Claude'a dla pozycji do wyjaśnienia (np. jakie tabele czyta dynamiczny SQL). Propozycje z celami
    pokazują się na mapie jako niepewne relacje, dopóki użytkownik ich nie zatwierdzi w UI."""
    with _analyzed_session() as s:
        return editing.propose(s, items)


# ---------------------------------------------------------------------------
# Zmiany od ostatniej analizy
# ---------------------------------------------------------------------------


@tool
def atlas_refresh() -> dict:
    """Odświeżenie: analizuje bieżący stan, porównuje z poprzednią analizą i zwraca zmiany na poziomie obiektów
    (dodane, zmienione, usunięte, przemianowane) z zależnymi obiektami i wstępną oceną ryzyka
    oraz listę commitów od poprzedniej analizy jako kontekst."""
    _require_engine()
    with _analyzed_session(NO_PREVIOUS_ANALYSIS) as s:
        if not current_snapshot(s):
            raise AtlasError(NO_PREVIOUS_ANALYSIS)

    result = refresh(ws())

    with ws().db.session() as s:
        return changeset_for_claude(s, ws().files.overrides(), result["changeset"])


@tool
def atlas_changes(
    changeset: Annotated[int | None, Field(description="Id zestawu zmian (domyślnie ostatni)")] = None,
) -> dict:
    """Zwraca zapisany zestaw zmian (domyślnie ostatni) w tym samym formacie co atlas_refresh, bez ponownej analizy.
    Użyj, gdy trzeba wrócić do opisu ostatnich zmian (np. w nowej sesji)."""
    with _analyzed_session(NO_CHANGES) as s:
        found = find_changeset(s, changeset)
        if not found:
            raise AtlasError(NO_CHANGES)
        return changeset_for_claude(s, ws().files.overrides(), found.id)


@tool
def atlas_save_change_notes(
    summary: Annotated[str | None, Field(description="Podsumowanie zestawu (2–4 zdania)")] = None,
    items: list[ChangeNote] | None = None,
    changeset: Annotated[int | None, Field(description="Id zestawu zmian (domyślnie ostatni)")] = None,
) -> dict:
    """Zapisuje podsumowanie zmian od Claude'a: ogólne podsumowanie zestawu
    i opis każdej zmiany z oceną ryzyka (high, med, low)."""
    with _analyzed_session(NO_CHANGESET) as s:
        found = find_changeset(s, changeset)
        if not found:
            raise AtlasError(NO_CHANGESET)
        return editing.save_change_notes(s, found, summary, items or [])


# ---------------------------------------------------------------------------
# Aplikacja webowa
# ---------------------------------------------------------------------------


@tool
def atlas_open_ui(
    view: Annotated[Literal["map", "changes", "issues", "guides"] | None, Field(description="Widok")] = None,
    object: Annotated[str | None, Field(description="Id obiektu do pokazania")] = None,  # noqa: A002
) -> dict:
    """Uruchamia lokalną aplikację SSDT Atlas i otwiera ją w przeglądarce. Zwraca adres."""
    return {
        "url": open_ui(ws(), view, object),
        "note": "Aplikacja działa lokalnie (127.0.0.1) do końca sesji Claude Code albo do atlas_close_ui.",
    }


@tool
def atlas_close_ui() -> dict:
    """Zamyka lokalną aplikację SSDT Atlas (serwer HTTP w tle) uruchomioną w tej sesji. Dane i analiza zostają;
    atlas_open_ui uruchomi ją ponownie."""
    return close_ui(ws())


# ---------------------------------------------------------------------------
# Pomocnicze
# ---------------------------------------------------------------------------


def _require_engine() -> None:
    status = engine_status()
    if not status["ok"]:
        raise AtlasError(status["error"])


def _bump() -> None:
    """Sygnał dla UI, że dane się zmieniły (np. nowe wskazówki)."""
    with ws().db.session() as s:
        bump_revision(s)
