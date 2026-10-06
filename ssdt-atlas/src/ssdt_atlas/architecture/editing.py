"""Zapis semantyki: agregacja architektury przez Claude'a (narzędzia MCP) i decyzje użytkownika (UI).

Claude zapisuje do bazy (domeny, przypisania, opisy, propozycje). Użytkownik zapisuje do overrides.json.
Claude nigdy nie nadpisuje poprawek użytkownika: zapis do bazy się odbywa, ale przy odczycie wygrywa
overrides.json (semantics/overlay.py), a Claude dostaje informację, które wartości są „zablokowane”.
"""

import time

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ssdt_atlas.architecture.lookup import require_analysis, resolve_object_id, slug
from ssdt_atlas.architecture.overlay import domains as merged_domains
from ssdt_atlas.core.database import bump_revision
from ssdt_atlas.core.errors import AtlasError
from ssdt_atlas.core.models import Change, Changeset, Domain, Issue, Note, Project, Proposal, Semantic
from ssdt_atlas.core.project_files import IssueOverride, NoteOverride, ProjectFiles, now_iso

# ---------------------------------------------------------------------------
# Dane wejściowe narzędzi Claude'a (schematy trafiają do opisu narzędzi MCP)
# ---------------------------------------------------------------------------


class DomainInput(BaseModel):
    project: str = Field(description="Projekt (baza), do którego należy domena")
    name: str = Field(description="Nazwa domeny, np. „Zamówienia”")
    description: str | None = Field(None, description="1–2 zdania: za co odpowiada domena")
    id: str | None = Field(None, description="Id istniejącej domeny (do aktualizacji)")
    order: int | None = Field(None, description="Kolejność wyświetlania (wg przepływu danych lub ważności)")


class AssignItem(BaseModel):
    object: str = Field(description="Id obiektu")
    domain: str | None = Field(None, description="Id domeny")
    description: str | None = Field(None, description="Opis obiektu z perspektywy biznesu (1–2 zdania)")


class DescribeItem(BaseModel):
    target: str = Field(description="„project:Nazwa”, „ext|nazwa” albo „domain:<id>”")
    description: str = Field(description="Opis")


class ProposalTarget(BaseModel):
    id: str = Field(description="Id obiektu docelowego")
    kind: str = Field("reads", description="reads, writes albo calls")


class ProposalInput(BaseModel):
    issue: str = Field(description="Klucz pozycji do wyjaśnienia")
    text: str = Field(description="Propozycja wyjaśnienia")
    targets: list[ProposalTarget] = Field(default_factory=list, description="Obiekty, których prawdopodobnie dotyczy")
    confidence: int | None = Field(None, ge=0, le=100, description="Pewność 0–100")


class ChangeNote(BaseModel):
    object: str = Field(description="Id obiektu")
    summary: str = Field(description="Co się zmieniło i jaki ma to wpływ")
    risk: str | None = Field(None, description="high, med albo low")


# ---------------------------------------------------------------------------
# Claude: domeny, przypisania, opisy, propozycje, notatki o zmianach
# ---------------------------------------------------------------------------


def save_domains(s: Session, items: list[DomainInput]) -> list[dict]:
    """Tworzy lub aktualizuje domeny. Id nowej domeny: „Projekt|slug-nazwy”."""
    snapshot = require_analysis(s)
    projects = list(s.scalars(select(Project.name).where(Project.snapshot_id == snapshot.id)))
    saved = []

    for item in items:
        project = next((p for p in projects if p.lower() == item.project.lower()), None)
        if not project:
            raise AtlasError(f"Nieznany projekt: {item.project}")

        domain_id = item.id or f"{project}|{slug(item.name)}"
        domain = s.get(Domain, domain_id)
        if domain:
            domain.name = item.name
            domain.description = item.description or domain.description
            domain.sort = item.order if item.order is not None else domain.sort
            domain.origin = "claude"
        else:
            s.add(Domain(id=domain_id, project=project, name=item.name, description=item.description, origin="claude",
                         sort=item.order if item.order is not None else 100))  # fmt: skip

        saved.append({"id": domain_id, "project": project, "name": item.name})

    bump_revision(s)
    return saved


def assign(s: Session, files: ProjectFiles, items: list[AssignItem]) -> dict:
    """Przypisuje obiekty do domen i nadaje opisy. Zwraca liczniki, błędy i listę zachowanych poprawek użytkownika."""
    require_analysis(s)
    ov = files.overrides()
    domains = merged_domains(s, ov)
    assigned = described = 0
    errors, kept = [], []

    for item in items:
        try:
            object_id = resolve_object_id(s, item.object)
        except AtlasError as e:
            errors.append(str(e))
            continue
        user = ov.objects.get(object_id)

        # --- domena ---
        if item.domain:
            domain = domains.get(item.domain)
            if not domain:
                errors.append(f"Nieznana domena {item.domain} ({item.object})")
            elif domain["project"] != object_id.split("|")[0]:
                errors.append(f"Domena {item.domain} należy do projektu {domain['project']}, a obiekt {object_id} nie")
            else:
                if user and user.domain:
                    kept.append(f"{object_id}: domena ustawiona przez użytkownika")
                _upsert_semantic(s, object_id, domain_id=item.domain, origin="claude")
                assigned += 1

        # --- opis ---
        if item.description:
            if user and user.description:
                kept.append(f"{object_id}: opis poprawiony przez użytkownika")
            _upsert_semantic(s, object_id, description=item.description)
            described += 1

    bump_revision(s)
    return {"assigned": assigned, "described": described, "errors": errors, "userOverridesKept": kept}


def describe(s: Session, items: list[DescribeItem]) -> dict:
    """Opisy baz („project:Nazwa”), systemów zewnętrznych („ext|nazwa”) i domen („domain:<id>”)."""
    described, errors = [], []

    for item in items:
        target = item.target
        if target.startswith("domain:"):
            domain = s.get(Domain, target.removeprefix("domain:"))
            if not domain:
                errors.append(f"Nieznana domena {target.removeprefix('domain:')}")
                continue
            domain.description = item.description

        elif target.startswith(("project:", "ext|")):
            s.merge(Note(target=target, description=item.description, origin="claude", updated_at=now_iso()))

        else:
            errors.append(f"Nieznany cel {target} (użyj project:Nazwa, ext|nazwa, domain:id)")
            continue

        described.append(target)

    bump_revision(s)
    return {"described": described, "errors": errors}


def propose(s: Session, items: list[ProposalInput]) -> dict:
    """Propozycje wyjaśnień; cele pojawiają się na mapie jako niepewne relacje, dopóki użytkownik ich nie zatwierdzi."""
    snapshot = require_analysis(s)
    issue_keys = set(s.scalars(select(Issue.key).where(Issue.snapshot_id == snapshot.id)))
    saved, errors = [], []

    for item in items:
        if item.issue not in issue_keys:
            errors.append(f"Nieznana pozycja {item.issue}")
            continue

        targets = []
        for target in item.targets:
            try:
                targets.append({"id": resolve_object_id(s, target.id), "kind": target.kind})
            except AtlasError as e:
                errors.append(str(e))

        s.merge(
            Proposal(
                issue_key=item.issue, text=item.text, targets=targets, confidence=item.confidence, updated_at=now_iso()
            )
        )
        saved.append(item.issue)

    bump_revision(s)
    return {"saved": saved, "errors": errors}


def save_change_notes(s: Session, changeset: Changeset, summary: str | None, items: list[ChangeNote]) -> dict:
    """Podsumowanie zestawu zmian i opisy poszczególnych zmian (z oceną ryzyka) od Claude'a."""
    if summary:
        changeset.summary = summary

    changes = {c.object_id: c for c in s.scalars(select(Change).where(Change.changeset_id == changeset.id))}
    updated, errors = 0, []

    for item in items:
        change = changes.get(item.object)
        if not change:
            # dopuszczamy samo „schemat.nazwa” bez nazwy projektu
            query = item.object.lower()
            matches = [c for oid, c in changes.items() if oid.lower() == query or oid.lower().endswith("|" + query)]
            if len(matches) != 1:
                errors.append(f"Brak zmiany dla {item.object}")
                continue
            change = matches[0]

        change.summary = item.summary
        change.risk = item.risk or change.risk
        updated += 1

    bump_revision(s)
    return {"changeset": changeset.id, "updated": updated, "errors": errors}


def _upsert_semantic(s: Session, object_id: str, **values) -> None:
    """Wstawia albo aktualizuje wiersz semantyki obiektu. Aktualizowane są tylko podane pola;
    nowy wiersz dostaje origin="claude", jeśli nie podano innego."""
    values["updated_at"] = now_iso()
    statement = sqlite_insert(Semantic).values(object_id=object_id, **({"origin": "claude"} | values))
    s.execute(statement.on_conflict_do_update(index_elements=["object_id"], set_=values))


# ---------------------------------------------------------------------------
# Użytkownik (UI): nowe domeny i rozstrzyganie pozycji do wyjaśnienia
# ---------------------------------------------------------------------------


def create_user_domain(s: Session, files: ProjectFiles, project: str, name: str, description: str | None) -> str:
    """Domena utworzona ręcznie w UI; nazwa trafia też do overrides.json, żeby przetrwała zmiany Claude'a."""
    domain_id = f"{project}|user-{_base36(int(time.time() * 1000))}"
    s.add(Domain(id=domain_id, project=project, name=name, description=description or None, origin="user", sort=50))
    files.set_override("domains", domain_id, "name", name)
    bump_revision(s)
    return domain_id


def resolve_issue(s: Session, files: ProjectFiles, issue: Issue, action: str, *, target: str | None = None,
                  text: str | None = None, kind: str | None = None, target_is_object: bool = False,
                  describes_external: bool = False) -> None:  # fmt: skip
    """Decyzja użytkownika o pozycji do wyjaśnienia.

    accept   zatwierdza propozycję Claude'a (jej cele stają się relacjami użytkownika)
    resolve  wyjaśnienie ręczne (opcjonalnie wskazany obiekt albo opis systemu zewnętrznego)
    skip     pomija pozycję
    reopen   cofa decyzję
    """
    with files.edit_overrides() as ov:
        if action == "reopen":
            ov.issues.pop(issue.key, None)

        elif action == "skip":
            ov.issues[issue.key] = _issue_entry("skip", "Pominięto")

        elif action == "accept":
            proposal = s.get(Proposal, issue.key)
            if not proposal:
                raise AtlasError("Brak propozycji")
            ov.issues[issue.key] = _issue_entry("done", f"Zatwierdzono propozycję Claude'a: {proposal.text}")
            for t in proposal.targets or []:
                ov.add_edge(issue.object_id, t["id"], t.get("kind") or "reads", "zatwierdzona propozycja")

        elif action == "resolve":
            resolution = " · ".join(x for x in (target, text) if x) or "Wyjaśniono"
            ov.issues[issue.key] = _issue_entry("done", resolution, target=target)
            if target and target_is_object and not target.startswith("ext|"):
                ov.add_edge(issue.object_id, target, kind or "reads", "wskazane przez użytkownika")
            if describes_external and issue.ext_id:
                note = ov.notes.get(issue.ext_id) or NoteOverride()
                note.description = text or target
                note.updatedAt = now_iso()
                ov.notes[issue.ext_id] = note

        else:
            raise AtlasError(f"Nieznana akcja: {action}")

    bump_revision(s)


def _issue_entry(status: str, resolution: str, target: str | None = None) -> IssueOverride:
    return IssueOverride(status=status, resolution=resolution, target=target, updatedAt=now_iso())


def _base36(number: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while number:
        number, rest = divmod(number, 36)
        out = digits[rest] + out
    return out or "0"
