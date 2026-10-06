"""Pierwszeństwo poprawek użytkownika przy odczycie (architecture/overlay.py): użytkownik > Claude > automat."""

from ssdt_atlas.architecture.overlay import domains, effective_domain, issue_status, note_of, semantic_of
from ssdt_atlas.core.models import DbObject, Domain, Issue, Note, Semantic
from ssdt_atlas.core.project_files import (
    DomainOverride,
    IssueOverride,
    NoteOverride,
    ObjectOverride,
    Overrides,
)

OBJECT = "Sales|sales.orders"


def sem(domain="Sales|zamowienia", description="Opis Claude'a", origin="claude") -> Semantic:
    return Semantic(object_id=OBJECT, domain_id=domain, description=description, origin=origin)


# ---------------------------------------------------------------------------
# Domena i opis obiektu
# ---------------------------------------------------------------------------


def test_semantic_from_claude_without_user_changes():
    result = semantic_of(OBJECT, sem(), Overrides())
    assert result == {
        "domain": "Sales|zamowienia",
        "domainOrigin": "claude",
        "description": "Opis Claude'a",
        "descOrigin": "claude",
    }


def test_user_description_wins_over_claude():
    ov = Overrides(objects={OBJECT: ObjectOverride(description="Opis użytkownika")})
    result = semantic_of(OBJECT, sem(), ov)
    assert (result["description"], result["descOrigin"]) == ("Opis użytkownika", "user")
    assert result["domainOrigin"] == "claude", "domena bez poprawki zostaje od Claude'a"


def test_user_domain_wins_over_claude():
    ov = Overrides(objects={OBJECT: ObjectOverride(domain="Sales|klienci")})
    result = semantic_of(OBJECT, sem(), ov)
    assert (result["domain"], result["domainOrigin"]) == ("Sales|klienci", "user")
    assert result["descOrigin"] == "claude"


def test_object_without_any_semantics():
    assert semantic_of(OBJECT, None, Overrides()) == {
        "domain": None,
        "domainOrigin": None,
        "description": None,
        "descOrigin": None,
    }


def test_description_origin_is_empty_when_there_is_no_description():
    assert semantic_of(OBJECT, sem(description=None, origin="auto"), Overrides())["descOrigin"] is None


def test_effective_domain_falls_back_to_schema_domain():
    obj = DbObject(id=OBJECT, project="Sales", schema_name="sales", name="Orders", type="Table", hash="h")
    known = {"Sales|zamowienia": {}}
    assert effective_domain(obj, {"domain": "Sales|zamowienia"}, known) == "Sales|zamowienia"
    assert effective_domain(obj, {"domain": "Sales|nie-ma"}, known) == "auto|Sales|sales"
    assert effective_domain(obj, {"domain": None}, known) == "auto|Sales|sales"


# ---------------------------------------------------------------------------
# Domeny, notatki, pozycje do wyjaśnienia
# ---------------------------------------------------------------------------


def test_domain_name_and_description_overrides(db):
    with db.session() as s:
        s.add(
            Domain(
                id="Sales|zamowienia",
                project="Sales",
                name="Zamówienia",
                description="Od Claude'a",
                origin="claude",
                sort=1,
            )
        )
        s.add(Domain(id="Sales|klienci", project="Sales", name="Klienci", origin="claude", sort=2))
        ov = Overrides(domains={"Sales|zamowienia": DomainOverride(name="Sprzedaż")})
        result = domains(s, ov)

    assert list(result) == ["Sales|zamowienia", "Sales|klienci"], "kolejność według sort"
    assert (result["Sales|zamowienia"]["name"], result["Sales|zamowienia"]["nameOrigin"]) == ("Sprzedaż", "user")
    assert (result["Sales|zamowienia"]["description"], result["Sales|zamowienia"]["descOrigin"]) == (
        "Od Claude'a",
        "claude",
    )


def test_note_precedence(db):
    with db.session() as s:
        s.add(Note(target="ext|crm", description="Od Claude'a", origin="claude"))
        assert note_of(s, Overrides(), "ext|crm") == {"description": "Od Claude'a", "origin": "claude", "name": None}

        renamed = Overrides(notes={"ext|crm": NoteOverride(name="Salesforce")})
        assert note_of(s, renamed, "ext|crm") == {
            "description": "Od Claude'a",
            "origin": "claude",
            "name": "Salesforce",
        }

        described = Overrides(notes={"ext|crm": NoteOverride(description="CRM firmy", name="Salesforce")})
        assert note_of(s, described, "ext|crm") == {"description": "CRM firmy", "origin": "user", "name": "Salesforce"}

        assert note_of(s, Overrides(), "project:Brak") == {"description": None, "origin": None, "name": None}


def test_issue_status_open_by_default():
    issue = Issue(key="k", object_id=OBJECT, kind="dynamic", ref="EXEC")
    assert issue_status(issue, Overrides()) == {"status": "open", "resolution": None, "by": None}


def test_issue_status_from_user_decision():
    issue = Issue(key="k", object_id=OBJECT, kind="dynamic", ref="EXEC")
    ov = Overrides(issues={"k": IssueOverride(status="skip", resolution="Pominięto")})
    assert issue_status(issue, ov) == {"status": "skip", "resolution": "Pominięto", "by": "user"}


def test_describing_external_system_closes_its_issues():
    issue = Issue(key="k", object_id=OBJECT, kind="external", ref="CRM", ext_id="ext|crm")
    ov = Overrides(notes={"ext|crm": NoteOverride(description="Salesforce")})
    assert issue_status(issue, ov) == {"status": "done", "resolution": "Opisano system: Salesforce", "by": "user"}

    only_name = Overrides(notes={"ext|crm": NoteOverride(name="Salesforce")})
    assert issue_status(issue, only_name)["status"] == "open", "sama nazwa nie wyjaśnia systemu"
