"""Porównanie dwóch analiz (change_analysis/compare.py) na danych syntetycznych: rodzaje zmian, szczegóły, ryzyko."""

import pytest

from sqlproj_atlas.change_analysis.compare import compare_snapshots

from builders import add_snapshot, col, edge, issue, obj, param, procedure, table


def compare(db, before: dict, after: dict) -> dict:
    """Porównuje dwa snapshoty opisane słownikami argumentów add_snapshot; wynik: id obiektu → zmiana."""
    with db.session() as s:
        old_id = add_snapshot(s, **before)
        new_id = add_snapshot(s, **after)
        return {c.object_id: c for c in compare_snapshots(s, old_id, new_id)}


ORDERS = "Sales|sales.orders"
REPORT = "Sales|rpt.vw_report"


# ---------------------------------------------------------------------------
# Rodzaje zmian
# ---------------------------------------------------------------------------


def test_identical_snapshots_have_no_changes(db):
    objects = [table(ORDERS), procedure("Sales|sales.usp_a")]
    assert compare(db, {"objects": objects}, {"objects": objects}) == {}


def test_added_object_with_dependencies(db):
    new = procedure("Sales|sales.usp_new")
    changes = compare(
        db,
        {"objects": [table(ORDERS)]},
        {"objects": [table(ORDERS), new], "edges": [edge(new["id"], ORDERS, "writes")]},
    )
    change = changes["Sales|sales.usp_new"]
    assert change.change_type == "added"
    assert change.details["depsAdded"] == [f"{ORDERS}|writes"]
    assert change.risk == "low"


def test_modified_object_when_definition_hash_changes(db):
    changes = compare(db, {"objects": [table(ORDERS, hash="v1")]}, {"objects": [table(ORDERS, hash="v2")]})
    assert changes[ORDERS].change_type == "modified"
    assert (changes[ORDERS].details["hashFrom"], changes[ORDERS].details["hashTo"]) == ("v1", "v2")
    assert changes[ORDERS].risk == "low"


def test_removed_object_without_dependents_is_low_risk(db):
    changes = compare(db, {"objects": [table(ORDERS), table("Sales|sales.old")]}, {"objects": [table(ORDERS)]})
    assert changes["Sales|sales.old"].change_type == "removed"
    assert changes["Sales|sales.old"].risk == "low"


def test_removed_object_with_remaining_dependents_is_medium_risk(db):
    report = obj(REPORT, "View")
    gone = obj("Sales|rpt.vw_gone", "View")
    changes = compare(
        db,
        {"objects": [table(ORDERS), report, gone], "edges": [edge(REPORT, ORDERS), edge(gone["id"], ORDERS)]},
        {"objects": [report]},
    )
    # zależny widok vw_gone też zniknął, więc nie jest liczony
    assert changes[ORDERS].details["dependents"] == [REPORT]
    assert changes[ORDERS].risk == "med"


def test_dependents_are_not_duplicated(db):
    report = obj(REPORT, "View")
    edges = [edge(REPORT, ORDERS, "reads"), edge(REPORT, ORDERS, "fk")]
    changes = compare(
        db,
        {"objects": [table(ORDERS, hash="v1"), report], "edges": edges},
        {"objects": [table(ORDERS, hash="v2"), report], "edges": edges},
    )
    assert changes[ORDERS].details["dependents"] == [REPORT]


# ---------------------------------------------------------------------------
# Zmiany nazw
# ---------------------------------------------------------------------------


def test_rename_detected_by_identical_definition(db):
    changes = compare(
        db,
        {"objects": [table("Sales|pay.transactions", hash="same")]},
        {"objects": [table("Sales|pay.payments", hash="same")]},
    )
    assert list(changes) == ["Sales|pay.payments"]
    assert changes["Sales|pay.payments"].change_type == "renamed"
    assert changes["Sales|pay.payments"].details["oldId"] == "Sales|pay.transactions"


def test_same_definition_but_different_type_is_not_a_rename(db):
    changes = compare(
        db,
        {"objects": [table("Sales|pay.transactions", hash="same")]},
        {"objects": [obj("Sales|pay.payments", "View", hash="same")]},
    )
    assert changes["Sales|pay.payments"].change_type == "added"
    assert changes["Sales|pay.transactions"].change_type == "removed"


def test_rename_from_refactorlog_even_when_definition_changed(db):
    rename = {"element": "[pay].[Transactions]", "newName": "[PaymentTransactions]", "type": "SqlTable"}
    changes = compare(
        db,
        {"objects": [table("Sales|pay.transactions", hash="old")]},
        {"objects": [table("Sales|pay.paymenttransactions", hash="new")], "renames": {"Sales": [rename]}},
    )
    change = changes["Sales|pay.paymenttransactions"]
    assert change.change_type == "renamed"
    assert change.details["oldId"] == "Sales|pay.transactions"


def test_refactorlog_entry_without_schema_is_ignored(db):
    rename = {"element": "[Transactions]", "newName": "[Payments]", "type": "SqlTable"}
    changes = compare(
        db,
        {"objects": [table("Sales|pay.transactions", hash="old")]},
        {"objects": [table("Sales|pay.payments", hash="new")], "renames": {"Sales": [rename]}},
    )
    assert changes["Sales|pay.payments"].change_type == "added"


# ---------------------------------------------------------------------------
# Kolumny, parametry, zależności
# ---------------------------------------------------------------------------


def test_column_changes(db):
    before = table(ORDERS, hash="v1", columns=[col("OrderId", pk=True), col("Discount"), col("Total", "decimal(10,2)")])
    after = table(ORDERS, hash="v2", columns=[col("OrderId", pk=True), col("Total", "decimal(18,2)"), col("Currency")])
    details = compare(db, {"objects": [before]}, {"objects": [after]})[ORDERS].details

    assert details["columnsAdded"] == ["Currency"]
    assert details["columnsRemoved"] == ["Discount"]
    assert details["columnsChanged"] == ["Total: decimal(10,2) → decimal(18,2)"]


def test_column_nullability_change_is_reported(db):
    before = table(ORDERS, hash="v1", columns=[col("Note", nullable=False)])
    after = table(ORDERS, hash="v2", columns=[col("Note", nullable=True)])
    assert compare(db, {"objects": [before]}, {"objects": [after]})[ORDERS].details["columnsChanged"] == [
        "Note: int → int"
    ]


def test_column_names_are_compared_case_insensitively(db):
    before = table(ORDERS, hash="v1", columns=[col("OrderId")])
    after = table(ORDERS, hash="v2", columns=[col("ORDERID")])
    details = compare(db, {"objects": [before]}, {"objects": [after]})[ORDERS].details
    assert (details["columnsAdded"], details["columnsRemoved"]) == ([], [])


def test_removed_column_is_medium_risk(db):
    before = table(ORDERS, hash="v1", columns=[col("OrderId"), col("Discount")])
    after = table(ORDERS, hash="v2", columns=[col("OrderId")])
    assert compare(db, {"objects": [before]}, {"objects": [after]})[ORDERS].risk == "med"


def test_parameter_changes(db):
    before = procedure("Sales|sales.usp_a", hash="v1", params=[param("@Id"), param("@Old")])
    after = procedure("Sales|sales.usp_a", hash="v2", params=[param("@Id"), param("@Currency")])
    details = compare(db, {"objects": [before]}, {"objects": [after]})["Sales|sales.usp_a"].details
    assert (details["paramsAdded"], details["paramsRemoved"]) == (["@Currency"], ["@Old"])


def test_dependency_changes(db):
    usp = "Sales|sales.usp_a"
    objects_before = [procedure(usp, hash="v1"), table(ORDERS), table("Sales|sales.items")]
    objects_after = [procedure(usp, hash="v2"), table(ORDERS), table("Sales|sales.items")]
    details = compare(
        db,
        {"objects": objects_before, "edges": [edge(usp, ORDERS, "reads")]},
        {"objects": objects_after, "edges": [edge(usp, ORDERS, "writes"), edge(usp, "Sales|sales.items", "reads")]},
    )[usp].details

    assert sorted(details["depsAdded"]) == ["Sales|sales.items|reads", f"{ORDERS}|writes"]
    assert details["depsRemoved"] == [f"{ORDERS}|reads"]


# ---------------------------------------------------------------------------
# Nowe problemy i ryzyko
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    ["sales.orders", "sales.orders.discount", "Sales.sales.orders"],
    ids=["obiekt", "kolumna obiektu", "z nazwą bazy"],
)
def test_broken_reference_makes_change_high_risk(db, ref):
    report = obj(REPORT, "View")
    broken = issue(REPORT, "unresolved", ref, "SQL71501")
    changes = compare(
        db,
        {"objects": [table(ORDERS, hash="v1"), report]},
        {"objects": [table(ORDERS, hash="v2"), report], "issues": [broken]},
    )
    assert changes[ORDERS].risk == "high"
    assert changes[ORDERS].details["brokenBy"] == [{"object": REPORT, "ref": ref, "line": 0, "message": "SQL71501"}]


def test_reference_to_old_name_breaks_renamed_object(db):
    report = obj(REPORT, "View")
    changes = compare(
        db,
        {"objects": [table("Sales|pay.transactions", hash="same"), report]},
        {
            "objects": [table("Sales|pay.payments", hash="same"), report],
            "issues": [issue(REPORT, "unresolved", "pay.transactions")],
        },
    )
    assert changes["Sales|pay.payments"].risk == "high"


def test_unrelated_broken_reference_does_not_affect_change(db):
    report = obj(REPORT, "View")
    changes = compare(
        db,
        {"objects": [table(ORDERS, hash="v1"), report]},
        {"objects": [table(ORDERS, hash="v2"), report], "issues": [issue(REPORT, "unresolved", "sales.ordersarchive")]},
    )
    assert changes[ORDERS].risk == "low"


def test_new_dynamic_sql_is_medium_risk(db):
    usp = "Sales|sales.usp_search"
    changes = compare(
        db,
        {"objects": [procedure(usp, hash="v1")]},
        {"objects": [procedure(usp, hash="v2")], "issues": [issue(usp, "dynamic", "EXEC(@sql)")]},
    )
    assert changes[usp].risk == "med"
    assert changes[usp].details["newDynamic"] == 1
    assert changes[usp].details["newIssues"] == [{"kind": "dynamic", "ref": "EXEC(@sql)", "message": ""}]


def test_issue_present_in_both_analyses_is_not_new(db):
    usp = "Sales|sales.usp_search"
    dynamic = issue(usp, "dynamic", "EXEC(@sql)")
    changes = compare(
        db,
        {"objects": [procedure(usp, hash="v1")], "issues": [dynamic]},
        {"objects": [procedure(usp, hash="v2")], "issues": [dynamic]},
    )
    assert changes[usp].risk == "low"
    assert changes[usp].details["newIssues"] == []
