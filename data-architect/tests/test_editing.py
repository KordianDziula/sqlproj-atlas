"""Zapis semantyki (architecture/editing.py): agregacja przez Claude'a i decyzje użytkownika.

Testy działają na prawdziwym projekcie po analizie (fixture `analyzed`).
"""

import pytest

from data_architect.architecture import editing
from data_architect.architecture.editing import (
    AssignItem,
    ChangeNote,
    DescribeItem,
    DomainInput,
    ProposalInput,
    ProposalTarget,
)
from data_architect.change_analysis.changes_view import find_changeset
from data_architect.core.database import revision
from data_architect.core.errors import AtlasError
from data_architect.core.models import Change, Domain, Issue, Note, Proposal, Semantic

from conftest import requires_engine

pytestmark = requires_engine

ORDERS = "Sales|sales.orders"


def issue_key(ws, object_id: str, kind: str) -> str:
    with ws.db.session() as s:
        return s.query(Issue).filter_by(object_id=object_id, kind=kind).first().key


# ---------------------------------------------------------------------------
# Domeny
# ---------------------------------------------------------------------------


def test_save_domains_creates_and_updates(analyzed):
    with analyzed.db.session() as s:
        rev = revision(s)
        saved = editing.save_domains(s, [DomainInput(project="sales", name="Zamówienia", description="Opis", order=2)])
        assert saved == [{"id": "Sales|zamowienia", "project": "Sales", "name": "Zamówienia"}]
        assert revision(s) == rev + 1

    with analyzed.db.session() as s:
        editing.save_domains(s, [DomainInput(id="Sales|zamowienia", project="Sales", name="Sprzedaż")])
        domain = s.get(Domain, "Sales|zamowienia")
        assert (domain.name, domain.description, domain.sort, domain.origin) == ("Sprzedaż", "Opis", 2, "claude")


def test_save_domains_rejects_unknown_project(analyzed):
    with analyzed.db.session() as s, pytest.raises(AtlasError, match="Nieznany projekt: Brak"):
        editing.save_domains(s, [DomainInput(project="Brak", name="X")])


def test_create_user_domain(analyzed):
    with analyzed.db.session() as s:
        domain_id = editing.create_user_domain(s, analyzed.files, "Sales", "Moja domena", None)
        domain = s.get(Domain, domain_id)
        assert domain_id.startswith("Sales|user-")
        assert (domain.origin, domain.sort) == ("user", 50)
    assert analyzed.files.overrides().domains[domain_id].name == "Moja domena"


# ---------------------------------------------------------------------------
# Przypisania i opisy
# ---------------------------------------------------------------------------


def test_assign_domain_and_description(analyzed):
    with analyzed.db.session() as s:
        editing.save_domains(s, [DomainInput(project="Sales", name="Zamówienia")])
        result = editing.assign(s, analyzed.files, [
            AssignItem(object="sales.Orders", domain="Sales|zamowienia", description="Nagłówek zamówienia."),
            AssignItem(object="sales.OrderItems", domain="Sales|zamowienia"),
        ])  # fmt: skip
        assert (result["assigned"], result["described"], result["errors"]) == (2, 1, [])
        semantic = s.get(Semantic, ORDERS)
        assert (semantic.domain_id, semantic.description, semantic.origin) == (
            "Sales|zamowienia",
            "Nagłówek zamówienia.",
            "claude",
        )


def test_assign_reports_errors_and_continues(analyzed):
    with analyzed.db.session() as s:
        editing.save_domains(s, [DomainInput(project="Sales", name="Zamówienia")])
        result = editing.assign(s, analyzed.files, [
            AssignItem(object="nie.istnieje", domain="Sales|zamowienia"),
            AssignItem(object=ORDERS, domain="Sales|brak"),
            AssignItem(object="Staging|stg.stg_orders", domain="Sales|zamowienia"),
            AssignItem(object=ORDERS, description="Opis"),
        ])  # fmt: skip

    assert result["described"] == 1
    assert result["assigned"] == 0
    assert len(result["errors"]) == 3
    assert "Nie znaleziono obiektu" in result["errors"][0]
    assert "Nieznana domena Sales|brak" in result["errors"][1]
    assert "należy do projektu Sales" in result["errors"][2]


def test_assign_reports_user_overrides(analyzed):
    analyzed.files.set_override("objects", ORDERS, "domain", "Sales|klienci")
    analyzed.files.set_override("objects", ORDERS, "description", "Od użytkownika")
    with analyzed.db.session() as s:
        editing.save_domains(s, [DomainInput(project="Sales", name="Zamówienia")])
        result = editing.assign(
            s, analyzed.files, [AssignItem(object=ORDERS, domain="Sales|zamowienia", description="Od Claude'a")]
        )

    assert result["userOverridesKept"] == [
        f"{ORDERS}: domena ustawiona przez użytkownika",
        f"{ORDERS}: opis poprawiony przez użytkownika",
    ]


def test_describe_targets(analyzed):
    with analyzed.db.session() as s:
        editing.save_domains(s, [DomainInput(project="Sales", name="Klienci")])
        result = editing.describe(s, [
            DescribeItem(target="project:Sales", description="Baza transakcyjna"),
            DescribeItem(target="ext|crm", description="Salesforce"),
            DescribeItem(target="domain:Sales|klienci", description="Klienci i zgody"),
            DescribeItem(target="domain:Sales|brak", description="x"),
            DescribeItem(target="cokolwiek", description="x"),
        ])  # fmt: skip
        assert result["described"] == ["project:Sales", "ext|crm", "domain:Sales|klienci"]
        assert result["errors"] == [
            "Nieznana domena Sales|brak",
            "Nieznany cel cokolwiek (użyj project:Nazwa, ext|nazwa, domain:id)",
        ]
        assert s.get(Note, "ext|crm").description == "Salesforce"
        assert s.get(Domain, "Sales|klienci").description == "Klienci i zgody"


# ---------------------------------------------------------------------------
# Propozycje i decyzje użytkownika o pozycjach do wyjaśnienia
# ---------------------------------------------------------------------------


def test_propose_resolves_targets_and_reports_errors(analyzed):
    key = issue_key(analyzed, "Sales|dbo.usp_purgetable", "dynamic")
    with analyzed.db.session() as s:
        result = editing.propose(s, [
            ProposalInput(issue=key, text="Czyści tabele", targets=[ProposalTarget(id="cfg.PurgeTables", kind="writes"),
                                                                    ProposalTarget(id="nie.ma")], confidence=40),
            ProposalInput(issue="brak|klucza", text="x"),
        ])  # fmt: skip
        assert result["saved"] == [key]
        assert len(result["errors"]) == 2
        proposal = s.get(Proposal, key)
        assert proposal.targets == [{"id": "Sales|cfg.purgetables", "kind": "writes"}]
        assert proposal.confidence == 40


def test_accept_proposal_adds_user_edges(analyzed):
    key = issue_key(analyzed, "Sales|dbo.usp_purgetable", "dynamic")
    with analyzed.db.session() as s:
        editing.propose(
            s,
            [
                ProposalInput(
                    issue=key, text="Czyści tabele", targets=[ProposalTarget(id="cfg.PurgeTables", kind="writes")]
                )
            ],
        )
        editing.resolve_issue(s, analyzed.files, s.query(Issue).filter_by(key=key).first(), "accept")

    ov = analyzed.files.overrides()
    assert ov.issues[key].status == "done"
    assert ov.issues[key].resolution == "Zatwierdzono propozycję Claude'a: Czyści tabele"
    assert [(e.source, e.to, e.kind) for e in ov.edges] == [
        ("Sales|dbo.usp_purgetable", "Sales|cfg.purgetables", "writes")
    ]


def test_accept_without_proposal_fails(analyzed):
    key = issue_key(analyzed, "Sales|dbo.usp_purgetable", "dynamic")
    with analyzed.db.session() as s, pytest.raises(AtlasError, match="Brak propozycji"):
        editing.resolve_issue(s, analyzed.files, s.query(Issue).filter_by(key=key).first(), "accept")


def test_resolve_manually_with_target_object(analyzed):
    key = issue_key(analyzed, "Sales|dbo.usp_purgetable", "dynamic")
    with analyzed.db.session() as s:
        issue = s.query(Issue).filter_by(key=key).first()
        editing.resolve_issue(
            s,
            analyzed.files,
            issue,
            "resolve",
            target="Sales|cfg.purgetables",
            text="Czyści konfigurację",
            kind="writes",
            target_is_object=True,
        )

    ov = analyzed.files.overrides()
    assert ov.issues[key].resolution == "Sales|cfg.purgetables · Czyści konfigurację"
    assert ov.issues[key].target == "Sales|cfg.purgetables"
    assert ov.edges[0].kind == "writes"


def test_resolve_without_details(analyzed):
    key = issue_key(analyzed, "Sales|dbo.usp_purgetable", "dynamic")
    with analyzed.db.session() as s:
        editing.resolve_issue(s, analyzed.files, s.query(Issue).filter_by(key=key).first(), "resolve")
    assert analyzed.files.overrides().issues[key].resolution == "Wyjaśniono"
    assert analyzed.files.overrides().edges == []


def test_describing_external_system_from_issue(analyzed):
    with analyzed.db.session() as s:
        issue = s.query(Issue).filter_by(ext_id="ext|crm").first()
        editing.resolve_issue(s, analyzed.files, issue, "resolve", text="Salesforce", describes_external=True)
    assert analyzed.files.overrides().notes["ext|crm"].description == "Salesforce"


def test_skip_and_reopen(analyzed):
    key = issue_key(analyzed, "Sales|dbo.usp_purgetable", "dynamic")
    with analyzed.db.session() as s:
        issue = s.query(Issue).filter_by(key=key).first()
        editing.resolve_issue(s, analyzed.files, issue, "skip")
        assert analyzed.files.overrides().issues[key].status == "skip"
        editing.resolve_issue(s, analyzed.files, issue, "reopen")
        assert key not in analyzed.files.overrides().issues


def test_unknown_action(analyzed):
    key = issue_key(analyzed, "Sales|dbo.usp_purgetable", "dynamic")
    with analyzed.db.session() as s, pytest.raises(AtlasError, match="Nieznana akcja: zrob"):
        editing.resolve_issue(s, analyzed.files, s.query(Issue).filter_by(key=key).first(), "zrob")


# ---------------------------------------------------------------------------
# Notatki o zmianach
# ---------------------------------------------------------------------------


def test_save_change_notes(refreshed):
    with refreshed.db.session() as s:
        changeset = find_changeset(s)
        result = editing.save_change_notes(s, changeset, "Podsumowanie", [
            ChangeNote(object="Sales|sales.orders", summary="Usunięto kolumnę", risk="high"),
            ChangeNote(object="pay.usp_refund", summary="Nowa procedura"),
            ChangeNote(object="nie.istnieje", summary="x"),
        ])  # fmt: skip
        assert (result["updated"], result["errors"]) == (2, ["Brak zmiany dla nie.istnieje"])
        assert changeset.summary == "Podsumowanie"

        refund = s.get(Change, (changeset.id, "Sales|pay.usp_refund"))
        assert (refund.summary, refund.risk) == ("Nowa procedura", "low"), "bez oceny ryzyka zostaje poprzednia"
