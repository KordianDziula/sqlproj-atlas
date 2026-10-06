"""Rozpoznawanie id obiektów podawanych przez Claude'a (architecture/lookup.py)."""

import pytest

from ssdt_atlas.architecture.lookup import require_analysis, resolve_object_id, slug
from ssdt_atlas.core.errors import AtlasError

from builders import add_snapshot, procedure, table


@pytest.fixture
def session(db):
    with db.session() as s:
        add_snapshot(
            s,
            [
                table("Sales|sales.orders"),
                table("Staging|stg.orders"),
                table("Sales|dbo.countries"),
                procedure("Sales|sales.usp_createorder"),
            ],
        )
        yield s


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Sales|sales.orders", "Sales|sales.orders"),
        ("sales|SALES.ORDERS", "Sales|sales.orders"),
        ("sales.Orders", "Sales|sales.orders"),
        ("[sales].[Orders]", "Sales|sales.orders"),
        ("  sales.orders  ", "Sales|sales.orders"),
        ("Countries", "Sales|dbo.countries"),
        ("usp_CreateOrder", "Sales|sales.usp_createorder"),
        ("stg.orders", "Staging|stg.orders"),
    ],
)
def test_resolves_object_id(session, raw, expected):
    assert resolve_object_id(session, raw) == expected


def test_ambiguous_name_lists_candidates(session):
    with pytest.raises(AtlasError, match="Niejednoznaczne id „orders”: .*Sales\\|sales.orders.*Staging\\|stg.orders"):
        resolve_object_id(session, "orders")


@pytest.mark.parametrize("raw", ["nie.istnieje", "brak"])
def test_unknown_object(session, raw):
    with pytest.raises(AtlasError, match="Nie znaleziono obiektu"):
        resolve_object_id(session, raw)


@pytest.mark.parametrize("raw", ["", None])
def test_missing_id(session, raw):
    with pytest.raises(AtlasError, match="Brak id obiektu"):
        resolve_object_id(session, raw)


def test_requires_analysis(db):
    with db.session() as s, pytest.raises(AtlasError, match="Brak analizy"):
        require_analysis(s)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Zamówienia", "zamowienia"),
        ("Słowniki i konfiguracja", "slowniki-i-konfiguracja"),
        ("  Płatności & zwroty!  ", "platnosci-zwroty"),
        ("ŁĄKA żółć", "laka-zolc"),
        ("KPI 2026", "kpi-2026"),
    ],
)
def test_slug(text, expected):
    assert slug(text) == expected
