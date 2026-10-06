---
name: init
description: Pierwsze uruchomienie SSDT Atlas w projekcie bazodanowym SSDT (SQL Server, pliki .sqlproj). Zbiera wskazówki od użytkownika, analizuje wszystkie bazy silnikiem Microsoft DacFx, agreguje architekturę w domeny biznesowe z opisami i otwiera mapę w przeglądarce. Użyj, gdy użytkownik chce zainicjalizować, przeanalizować lub zmapować projekt SSDT po raz pierwszy.
argument-hint: "[dodatkowe wskazówki]"
---

# SSDT Atlas: pierwsza analiza projektu

Jesteś odpowiedzialny za **agregację architektury**: silnik Microsoftu (DacFx + ScriptDom) dostarcza fakty (obiekty, kolumny, relacje, nierozwiązane odwołania), a Ty tworzysz z nich czytelną mapę: domeny biznesowe, opisy, wyjaśnienia. Użytkownik może później wszystko poprawić w UI, a jego poprawki mają pierwszeństwo.

Narzędzia MCP serwera `atlas` (pełne nazwy zaczynają się od `mcp__plugin_ssdt-atlas_atlas__`): `atlas_status`, `atlas_analyze`, `atlas_configure`, `atlas_guidelines`, `atlas_list_objects`, `atlas_get_object`, `atlas_save_domains`, `atlas_assign`, `atlas_describe`, `atlas_issues`, `atlas_propose`, `atlas_open_ui`.

Dodatkowe wskazówki od użytkownika (mogą być puste): $ARGUMENTS

Komunikuj się po polsku. Pracuj partiami i informuj krótko o postępie („Analizuję…”, „Agreguję domeny w bazie Sales…”).

## Krok 1: Stan

Wywołaj `atlas_status`.
- Narzędzia `atlas` są niedostępne (serwer się nie uruchomił, zwykle brak uv albo nieprzygotowane środowisko) albo `engine.ok = false` (zwykle brak .NET SDK 8+ albo silnik nie jest jeszcze zbudowany na tym komputerze) → wykonaj kroki skilla `/ssdt-atlas:setup` (sprawdzenie, zgoda użytkownika, instalacja). Kontynuuj inicjalizację dopiero, gdy silnik działa.
- `sqlProjectsFound` puste → to nie jest projekt SSDT (brak plików .sqlproj). Powiedz to i zakończ.
- `initialized = true` i `guidelines = true` → projekt był już analizowany. Zaproponuj `/ssdt-atlas:refresh` (zmiany od ostatniej analizy). Kontynuuj pełną inicjalizację tylko, jeśli użytkownik wyraźnie tego chce.

## Krok 2: Analiza silnikiem

Wywołaj `atlas_analyze`. Z podsumowania odczytaj: projekty (bazy) i ich formaty, schematy z przykładowymi nazwami, prefiksy nazw (np. `usp_`, `vw_`, `stg_`), zmienne SQLCMD i odwołania między bazami, systemy zewnętrzne (linked servery, bazy spoza solucji), pozycje do wyjaśnienia.

## Krok 3: Wywiad (wskazówki interpretacji)

Zadaj pytania narzędziem `AskUserQuestion` (maksymalnie 4 pytania na wywołanie, więc najwyżej 2 wywołania). **Każde pytanie ma gotowe propozycje odpowiedzi** wywnioskowane z podsumowania, tak żeby użytkownik głównie potwierdzał. Zakres:
1. **Bazy**: które analizować, które pominąć (np. projekty testowe) i jak płyną dane między bazami (np. Sales → Staging → DWH, na podstawie odwołań).
2. **Domeny**: czy schemat to domena biznesowa? Jak podzielić schemat `dbo` (np. według prefiksów)?
3. **Konwencje nazw**: co oznaczają wykryte prefiksy i sufiksy (np. `_old`, `_bak` = do usunięcia).
4. **Systemy zewnętrzne**: czym są wykryte linked servery i zmienne SQLCMD bez projektu.
5. **Słownik i styl**: skróty biznesowe, język opisów (domyślnie polski), czy coś pominąć.

Jeśli `AskUserQuestion` jest niedostępne albo sesja jest nieinteraktywna, nie czekaj: przyjmij rozsądne założenia na podstawie analizy i zapisz je jako założenia do potwierdzenia.

Zapisz wskazówki `atlas_guidelines` (`action: save`) jako Markdown z sekcjami `## Tytuł`, np. „Bazy w solucji”, „Podział na domeny”, „Konwencje nazw”, „Co pomijamy”, „Systemy zewnętrzne”, „Słownik pojęć”, „Styl opisów”. Treść: konkretne, krótkie zdania.

Jeśli użytkownik chce pominąć projekty: `atlas_configure` z `excludeProjects`, a potem ponownie `atlas_analyze`.

## Krok 4: Agregacja architektury (najważniejsze)

Dla **każdej bazy (projektu)** osobno:
1. Pobierz obiekty: `atlas_list_objects` z `project`, `limit: 150`, kolejne partie przez `offset` aż do końca.
2. Zaprojektuj **domeny biznesowe** (zwykle 3–12 na bazę): obszary odpowiedzialności rozpoznawalne dla biznesu (np. „Zamówienia”, „Klienci”, „Płatności”, „Słowniki i konfiguracja”). Kieruj się wskazówkami użytkownika, schematami, klastrami kluczy obcych (tabele powiązane FK zwykle są w jednej domenie), tym, z jakich tabel korzystają procedury, oraz nazwami. Unikaj domen jednoobiektowych i wora „Inne”, chyba że naprawdę nie da się inaczej.
3. Zapisz domeny: `atlas_save_domains` (nazwa + opis 1–2 zdania z perspektywy biznesu, `order` zgodny z przepływem danych lub ważnością).
4. Przypisz **wszystkie** obiekty: `atlas_assign` partiami po maksymalnie 100 pozycji. Do każdego obiektu dodaj **opis** (1–2 zdania: co robi dla firmy, bez powtarzania nazwy obiektu). W bardzo dużych bazach (ponad 400 obiektów) opisz co najmniej wszystkie tabele i obiekty z największą liczbą powiązań, a pozostałym przypisz tylko domenę.
5. Sprawdź `stillInAutoDomainsByProject` w odpowiedzi `atlas_assign`: dla bieżącej bazy ma nie być wpisu (0 obiektów bez domeny). Jeśli są, przypisz brakujące (`atlas_list_objects` z `project` i `unassigned: true`).

Pole `userLocked` oznacza poprawki użytkownika. Nie próbuj ich zmieniać.

## Krok 5: Opisy baz i systemów zewnętrznych

`atlas_describe`: dla każdej bazy (`project:Nazwa`) 1–2 zdania o jej roli; dla każdego systemu zewnętrznego (`ext|nazwa`) ostrożny opis na podstawie użycia i wskazówek. Użytkownik potwierdzi opis systemu w UI.

## Krok 6: Pozycje do wyjaśnienia

`atlas_issues` (otwarte). Dla dynamicznego SQL i nierozwiązanych odwołań przeczytaj kod (`atlas_get_object`) i zaproponuj wyjaśnienie `atlas_propose`:
- `text`: co robi ten fragment i do czego się odwołuje,
- `targets`: obiekty, których prawdopodobnie dotyczy (id + rodzaj `reads`/`writes`/`calls`), tylko gdy masz podstawy,
- `confidence`: uczciwa pewność 0–100.
Dla systemów zewnętrznych wystarczy opis w kroku 5.

## Krok 7: UI i podsumowanie

Wywołaj `atlas_open_ui` (`view: map`) i podaj adres. Na koniec krótko w czacie:
- ile baz, domen i obiektów,
- jakie domeny powstały (po kilka na bazę),
- ile pozycji czeka na wyjaśnienie i gdzie je znaleźć (zakładka „Do wyjaśnienia”),
- co dalej: `/ssdt-atlas:refresh` po nowych commitach.
