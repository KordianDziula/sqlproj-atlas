"""Domeny automatyczne (architecture/auto_domains.py): każdy obiekt bez domeny trafia do domeny swojego schematu."""

from sqlproj_atlas.architecture.auto_domains import auto_domain_id, ensure_auto_domains
from sqlproj_atlas.core.models import Domain, Semantic

from builders import add_snapshot, table


def test_auto_domain_id():
    assert auto_domain_id("Sales", "CRM") == "auto|Sales|crm"


def test_objects_without_domain_get_schema_domain(db):
    with db.session() as s:
        sid = add_snapshot(s, [table("Sales|crm.customers"), table("Sales|crm.addresses"), table("Sales|cat.products")])
        ensure_auto_domains(s, sid)

        crm = s.get(Domain, "auto|Sales|crm")
        assert (crm.name, crm.origin, crm.sort, crm.project) == ("crm", "auto", 1000, "Sales")
        assert s.get(Semantic, "Sales|crm.addresses").domain_id == "auto|Sales|crm"
        assert s.get(Semantic, "Sales|cat.products").domain_id == "auto|Sales|cat"


def test_domain_assigned_by_claude_is_kept(db):
    with db.session() as s:
        s.add(Domain(id="Sales|klienci", project="Sales", name="Klienci", origin="claude", sort=1))
        s.add(Semantic(object_id="Sales|crm.customers", domain_id="Sales|klienci", origin="claude"))
        sid = add_snapshot(s, [table("Sales|crm.customers")])
        ensure_auto_domains(s, sid)

        assert s.get(Semantic, "Sales|crm.customers").domain_id == "Sales|klienci"
        assert s.get(Domain, "auto|Sales|crm") is None


def test_object_in_deleted_domain_falls_back_to_schema_domain(db):
    with db.session() as s:
        s.add(
            Semantic(object_id="Sales|crm.customers", domain_id="Sales|usunieta", origin="claude", description="Opis")
        )
        sid = add_snapshot(s, [table("Sales|crm.customers")])
        ensure_auto_domains(s, sid)

        semantic = s.get(Semantic, "Sales|crm.customers")
        assert semantic.domain_id == "auto|Sales|crm"
        assert semantic.description == "Opis", "opis zostaje"


def test_running_twice_changes_nothing(db):
    with db.session() as s:
        sid = add_snapshot(s, [table("Sales|crm.customers")])
        ensure_auto_domains(s, sid)
        ensure_auto_domains(s, sid)
        assert len(s.query(Domain).all()) == 1
