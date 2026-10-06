"""Testy właściwości (Hypothesis): tysiące losowych danych dla parserów i reguł, które muszą działać zawsze.

Zamiast pojedynczych przykładów opisujemy cechę („dla dowolnego tekstu wynik zawiera tylko a-z, 0-9 i myślniki”),
a Hypothesis szuka danych, które ją łamią, i upraszcza je do najmniejszego kontrprzykładu.
"""

import string

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from data_architect.architecture.lookup import resolve_object_id, slug
from data_architect.architecture.map_view import _order_by_data_flow
from data_architect.change_analysis.compare import compare_snapshots
from data_architect.change_analysis.definition_diff import definition_diff
from data_architect.core.database import Database
from data_architect.core.project_files import ProjectFiles
from data_architect.project_analysis.sqlproj import _glob_match, _join

from builders import add_snapshot, table

# fragment ścieżki lub nazwy: litery (także polskie), cyfry, podkreślenie, spacja
NAME = (
    st.text(alphabet=string.ascii_letters + string.digits + "_ ąęłńóśźż", min_size=1, max_size=12)
    .map(str.strip)
    .filter(bool)
)
LINE_TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r\n"), max_size=40)
DB_SETTINGS = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


SQL_LINES = st.lists(st.sampled_from(["SELECT 1", "FROM t", "-- komentarz", "", "WHERE x = 1", "GO"]) | LINE_TEXT,
                     min_size=1, max_size=25)  # fmt: skip


@given(SQL_LINES, SQL_LINES)
def test_definition_diff_rebuilds_both_versions(before, after):
    # z pełnym kontekstem różnica zawiera całe obie wersje: kontekst + usunięte = „było”, kontekst + dodane = „jest”
    old, new = "\n".join(before), "\n".join(after)
    lines = definition_diff(old, new, context=10_000)
    if old == new or not old or not new:
        return
    assert [t for k, t in lines if k in "cd"] == old.split("\n")
    assert [t for k, t in lines if k in "ca"] == new.split("\n")


@given(SQL_LINES)
def test_definition_diff_of_same_text_is_empty(lines):
    text = "\n".join(lines)
    assert definition_diff(text, text) == []
    assert definition_diff(text, text.replace("\n", "\r\n")) == [], "końce linii Windows bez znaczenia"


# ---------------------------------------------------------------------------
# Ścieżki i wzorce MSBuild
# ---------------------------------------------------------------------------


@given(st.lists(NAME, min_size=1, max_size=5))
def test_glob_globstar_matches_any_path(parts):
    path = "/".join(parts) + ".sql"
    assert _glob_match(path, "**")
    assert _glob_match(path, "**/*.sql")
    assert _glob_match(path, path.upper()), "wielkość liter bez znaczenia"


@given(
    st.lists(st.sampled_from(["a", "b", "..", ".", "c d"]), max_size=8),
    st.lists(st.sampled_from(["x", "..", "y"]), max_size=4),
)
def test_joined_paths_are_normalized(relative, base):
    joined = _join("/".join(base), "\\".join(relative))
    assert ".." not in joined.split("/")
    assert "." not in joined.split("/")
    assert not joined.startswith("/") and "\\" not in joined


# ---------------------------------------------------------------------------
# Nazwy i identyfikatory
# ---------------------------------------------------------------------------


@given(st.text(max_size=60))
def test_slug_is_safe_and_stable(text):
    result = slug(text)
    assert set(result) <= set(string.ascii_lowercase + string.digits + "-")
    assert not result.startswith("-") and not result.endswith("-")
    assert slug(result) == result


@given(st.lists(st.tuples(NAME, NAME), min_size=1, max_size=15, unique_by=lambda t: (t[0].lower(), t[1].lower())))
@DB_SETTINGS
def test_every_object_resolves_by_full_id_in_any_letter_case(tmp_path_factory, names):
    db = Database(tmp_path_factory.mktemp("prop") / "t.db")
    try:
        ids = [f"Db|{schema.lower()}.{name.lower()}" for schema, name in names]
        with db.session() as s:
            add_snapshot(s, [table(i) for i in ids])
            for object_id in ids:
                assert resolve_object_id(s, object_id.upper()) == object_id
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# Kolejność baz
# ---------------------------------------------------------------------------


@given(st.lists(st.text(alphabet=string.ascii_uppercase, min_size=1, max_size=3), unique=True, max_size=8), st.data())
def test_database_order_is_a_permutation_respecting_data_flow(names, data):
    # krawędzie tylko „wstecz” w losowej kolejności → graf bez cykli
    ranked = data.draw(st.permutations(names))
    pairs = [(a, b) for i, a in enumerate(ranked) for b in ranked[:i]]
    chosen = data.draw(st.lists(st.sampled_from(pairs), max_size=10)) if pairs else []
    edges = [{"src": f"{a}|x.y", "dst": f"{b}|x.y"} for a, b in chosen]

    order = _order_by_data_flow(names, edges)
    assert sorted(order) == sorted(names)
    for consumer, source in chosen:
        assert order.index(source) < order.index(consumer)


# ---------------------------------------------------------------------------
# Porównanie analiz
# ---------------------------------------------------------------------------


@given(st.sets(st.integers(0, 30), max_size=12), st.sets(st.integers(0, 30), max_size=12))
@DB_SETTINGS
def test_compare_counts_added_and_removed(tmp_path_factory, before, after):
    db = Database(tmp_path_factory.mktemp("prop") / "t.db")
    try:
        with db.session() as s:
            old = add_snapshot(s, [table(f"Db|dbo.t{i}") for i in before])
            new = add_snapshot(s, [table(f"Db|dbo.t{i}") for i in after])
            changes = compare_snapshots(s, old, new)
            back = compare_snapshots(s, new, old)
            same = compare_snapshots(s, old, old)

        kinds = sorted((c.object_id, c.change_type) for c in changes)
        assert kinds == sorted(
            [(f"Db|dbo.t{i}", "added") for i in after - before] + [(f"Db|dbo.t{i}", "removed") for i in before - after]
        )
        assert len(back) == len(changes), "porównanie w drugą stronę zamienia dodane z usuniętymi"
        assert same == []
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# Poprawki użytkownika
# ---------------------------------------------------------------------------


@given(
    st.dictionaries(
        st.text(alphabet=string.ascii_letters + "|.", min_size=1, max_size=20),
        st.text(min_size=1, max_size=50),
        max_size=10,
    )
)
@DB_SETTINGS
def test_overrides_round_trip_any_text(tmp_path_factory, descriptions):
    files = ProjectFiles(tmp_path_factory.mktemp("prop"))
    for key, text in descriptions.items():
        files.set_override("objects", key, "description", text)

    stored = {k: v.description for k, v in files.overrides().objects.items()}
    assert stored == descriptions
