"""Testy interfejsu w prawdziwej przeglądarce (Playwright + Chromium): klikamy jak użytkownik.

Każdy test dostaje świeżą kopię projektu po odświeżeniu (fixture `refreshed`) i własny serwer aplikacji.
Błędy JavaScriptu na stronie oblewają test. Przeglądarkę instaluje się raz poleceniem:

    uv run playwright install chromium
"""

import json
import re

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright

from sqlproj_atlas.web_app.runner import ensure_ui

from conftest import requires_engine

pytestmark = [requires_engine, pytest.mark.ui]

ORDERS = "Sales|sales.orders"


# ---------------------------------------------------------------------------
# Przeglądarka i strona
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError:
            pytest.skip("brak przeglądarki Playwright (uv run playwright install chromium)")
        yield browser
        browser.close()


def open_page(browser, ws, hash_: str = ""):
    """Strona aplikacji dla Workspace; zbiera błędy JavaScriptu."""
    url = ensure_ui(ws)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.on("console", lambda message: page.errors.append(message.text) if message.type == "error" else None)
    page.goto(url + hash_)
    return page


@pytest.fixture
def page(browser, refreshed):
    page = open_page(browser, refreshed)
    page.ws = refreshed
    expect(page.locator("#ffront .node").first).to_be_visible()
    yield page
    assert page.errors == [], "błędy JavaScriptu na stronie"
    page.context.close()


def node(page: Page, object_id: str):
    return page.locator(f'#ffront .node[data-id="{object_id}"]')


def go_to(page: Page, view: str) -> None:
    page.locator(f'#nav button[data-v="{view}"]').click()


# ---------------------------------------------------------------------------
# Mapa
# ---------------------------------------------------------------------------


def test_map_shows_databases_and_objects(page):
    expect(page.locator("#titlebar h2")).to_have_text("Mapa")
    expect(page.locator("#titlebar .sub")).to_contain_text("3 bazy")
    expect(page.locator("#projName")).to_have_text("sklep")
    expect(node(page, ORDERS)).to_be_visible()
    assert page.locator("#ffront .node").count() >= 67


def test_names_fit_inside_tiles(page):
    # nazwy dopasowane do szerokości kafelka (także bardzo długie i z szerokimi znakami), bez nachodzenia tekstów
    page.evaluate(
        """() => {
            M.objects[0].name = 'usp_BardzoDlugaNazwaProceduryKtoraNieMiesciSieWKafelku';
            M.objects[1].name = 'WWWWWWWWWWWWWWWWWWWWWWWWWWWW';
            M.domains[0].name = 'Bardzo długa nazwa domeny biznesowej, która na pewno się nie zmieści';
            drawMap();
        }"""
    )
    problems = page.evaluate(
        """() => {
            const out = [];
            for (const g of document.querySelectorAll('#fback g, #ffront g')) {
                const rect = g.querySelector(':scope > rect'); if (!rect) continue;
                const rb = rect.getBBox(), texts = [...g.querySelectorAll(':scope > text')];
                for (const t of texts) {
                    const tb = t.getBBox();
                    if (!t.textContent.trim()) out.push('pusty tekst');
                    if (tb.x + tb.width > rb.x + rb.width + 0.5) out.push('wystaje: ' + t.textContent);
                }
                for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++) {
                    const a = texts[i].getBBox(), b = texts[j].getBBox();
                    if (a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height)
                        out.push('nachodzi: ' + texts[i].textContent + ' / ' + texts[j].textContent);
                }
            }
            return out;
        }"""
    )
    assert problems == []
    expect(page.locator("#fback text", has_text="Bardzo długa nazwa")).to_contain_text("…")


def test_excluded_database_is_a_grey_tile(page):
    # projekt Tests jest pominięty w config.json fixtury: szary kafelek bez interakcji
    tile = page.locator("#fback g.excl", has_text="Tests")
    expect(tile).to_contain_text("Baza pominięta w analizie")
    expect(tile).not_to_have_attribute("data-id", re.compile(".*"))


def test_clicking_object_opens_panel_with_relations(page):
    node(page, ORDERS).click()

    panel = page.locator("#panel")
    expect(panel).to_be_visible()
    expect(panel.locator("h3")).to_have_text("sales.Orders")
    expect(panel).to_contain_text("usp_CreateOrder")
    expect(page).to_have_url(re.compile(r"#map/Sales(\||%7C)sales\.orders$"))

    page.locator("[data-close]").click()
    expect(panel).to_be_hidden()


def test_search_selects_object(page):
    page.locator("#search").fill("usp_CreateOrder")
    page.locator("#search").press("Enter")
    expect(page.locator("#panel h3")).to_have_text("sales.usp_CreateOrder")


def test_search_without_result_shows_message(page):
    page.locator("#search").fill("nie-ma-takiego-obiektu")
    page.locator("#search").press("Enter")
    expect(page.locator("#toast")).to_contain_text("Nie znaleziono")


def test_editing_description_saves_user_override(page):
    node(page, ORDERS).click()
    page.locator(f'[data-edit="obj:{ORDERS}"]').click()
    page.locator(f'textarea[id="edit-obj:{ORDERS}"]').fill("Nagłówek zamówienia (opis użytkownika)")
    page.locator(f'[data-save="obj:{ORDERS}"]').click()

    expect(page.locator("#toast")).to_contain_text("Zapisano")
    expect(page.locator("#panel")).to_contain_text("poprawione przez Ciebie")
    overrides = json.loads(page.ws.files.overrides_file.read_text(encoding="utf-8"))
    assert overrides["objects"][ORDERS]["description"] == "Nagłówek zamówienia (opis użytkownika)"


def test_deep_link_opens_object(browser, refreshed):
    page = open_page(browser, refreshed, "#map/Sales%7Csales.orders")
    try:
        expect(page.locator("#panel h3")).to_have_text("sales.Orders")
        assert page.errors == []
    finally:
        page.context.close()


def test_escape_closes_panel(page):
    node(page, ORDERS).click()
    expect(page.locator("#panel")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#panel")).to_be_hidden()


# ---------------------------------------------------------------------------
# Zmiany
# ---------------------------------------------------------------------------


def test_changes_show_objects_with_diff_and_commit_list(page):
    go_to(page, "changes")
    expect(page.locator("#titlebar h2")).to_have_text("Zmiany od ostatniej analizy")

    changes = page.locator("[data-change]")
    expect(changes.first).to_have_attribute("aria-expanded", "true")
    expect(page.locator(".fold.open")).to_have_count(1)

    changes.first.click()  # ponowne kliknięcie zwija
    expect(page.locator(".fold.open")).to_have_count(0)

    orders = page.locator('[data-change="Sales|sales.orders"]')
    orders.click()
    expect(orders).to_have_attribute("aria-expanded", "true")
    expect(page.locator(".fold.open .diff")).to_contain_text("DiscountCode")

    expect(page.locator(".chg-db .sec-h .db")).to_have_text(["Sales"])  # zmiany fazy B są tylko w bazie Sales
    expect(page.locator(".chg-db").first.locator(".fold-h .name").first).not_to_contain_text("Sales.")

    # kilka baz: grupy w kolejności przepływu danych (Sales → Staging → DWH), w grupie bez nazwy bazy
    page.evaluate(
        """async () => {
            CACHE.changes.changes[0].project = 'DWH';
            CACHE.changes.changes[1].project = 'Staging';
            await render();
        }"""
    )
    expect(page.locator(".chg-db .sec-h .db")).to_have_text(["Sales", "Staging", "DWH"])
    page.evaluate("async () => { CACHE.changes = null; await render(); }")

    commits = page.locator(".clist .commit")
    expect(commits).to_have_count(7)
    expect(commits.first).to_contain_text("Index for stock lookups")


# ---------------------------------------------------------------------------
# Do wyjaśnienia
# ---------------------------------------------------------------------------


def test_issues_skip_and_reopen(page):
    go_to(page, "issues")
    expect(page.locator("#titlebar h2")).to_have_text("Do wyjaśnienia")
    open_before = page.locator("[data-u]").count()
    assert open_before > 0

    page.locator("[data-u]").first.locator('[data-uact="skip"]').click()
    expect(page.locator("[data-u]")).to_have_count(open_before - 1)

    page.locator('[data-utab="skip"]').click()
    expect(page.locator("[data-u]")).to_have_count(1)
    page.locator('[data-uact="reopen"]').click()
    expect(page.locator("[data-u]")).to_have_count(0)


def test_issue_details_show_full_definition(page):
    go_to(page, "issues")
    first = page.locator("[data-u]").first
    first.locator('[data-uact="details"]').click()
    expect(page.locator("[data-u]").first).to_contain_text("CREATE")


def test_describing_external_system(page):
    go_to(page, "issues")
    external = page.locator("[data-u]", has_text="CRM").first
    external.locator('[data-uact="form"]').click()
    page.locator("#uf-text").fill("Salesforce")
    page.locator('[data-uact="save"]').click()

    expect(page.locator("#toast")).to_contain_text("Zapisano")
    assert page.ws.files.overrides().notes["ext|crm"].description == "Salesforce"


# ---------------------------------------------------------------------------
# Wskazówki, motyw, odświeżenie, aktualizacja na żywo
# ---------------------------------------------------------------------------


def test_adding_guideline(page):
    go_to(page, "guides")
    page.locator('[data-edit="gnew"]').click()
    page.locator("#edit-gnew-title").fill("Konwencje nazw")
    page.locator("#edit-gnew").fill("usp_ to procedury biznesowe")
    page.locator('[data-save="gnew"]').click()

    expect(page.locator(".guide")).to_contain_text("Konwencje nazw")
    assert "## Konwencje nazw" in page.ws.files.guidelines()


def test_theme_is_remembered(page):
    page.locator('#themeSeg button[data-t="dark"]').click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


def test_ui_has_no_buttons_running_analysis(page):
    expect(page.locator("#ffront .node").first).to_be_visible()
    expect(page.locator("[data-refresh], [data-analyze]")).to_have_count(0)


def test_changes_from_claude_appear_without_reload(page):
    page.ws.files.set_override("objects", ORDERS, "description", "Opis zapisany poza przeglądarką")
    from sqlproj_atlas.core.database import bump_revision

    with page.ws.db.session() as s:
        bump_revision(s)

    node(page, ORDERS).click()
    expect(page.locator("#panel")).to_contain_text("Opis zapisany poza przeglądarką", timeout=10_000)


def test_empty_project_explains_how_to_start(browser, workspace):
    page = open_page(browser, workspace)
    try:
        expect(page.locator(".empty-state")).to_contain_text("/sqlproj-atlas:init")
        expect(page.locator("[data-analyze]")).to_have_count(0)
        assert page.errors == []
    finally:
        page.context.close()
