---
name: refresh
description: Odświeża analizę Data Architect. Porównuje bieżący stan projektu SSDT z poprzednią analizą („było → jest”), opisuje zmiany i ich wpływ (np. usunięta kolumna nadal używana w procedurach), przypisuje nowe obiekty do domen i otwiera widok Zmiany. Użyj, gdy użytkownik pyta, co się zmieniło w bazach, albo chce zaktualizować mapę.
---

# Data Architect: odświeżenie i opis zmian

Komunikuj się po polsku. Narzędzia MCP serwera `atlas` (pełne nazwy zaczynają się od `mcp__plugin_data-architect_atlas__`).

## Krok 1: Stan

`atlas_status`.
- `initialized = false` → poproś o `/data-architect:init` i zakończ.
- `engine.ok = false` → przekaż komunikat błędu, zaproponuj `/data-architect:setup` (np. po aktualizacji wtyczki silnik trzeba zbudować ponownie) i zakończ.

## Krok 2: Zmiany

- Jeśli `newCommitsSinceAnalysis > 0` albo `uncommittedChanges = true` → `atlas_refresh` (nowa analiza i porównanie).
- W przeciwnym razie sprawdź ostatni zestaw zmian: `atlas_changes`. Jeśli ma zmiany bez podsumowania (np. odświeżenie przerwane w poprzedniej sesji), opisz go. Jeśli wszystko jest już opisane, powiedz, że od ostatniej analizy nic się nie zmieniło, i zaproponuj otwarcie UI.

## Krok 3: Kontekst

`atlas_guidelines` (`get`), żeby trzymać się wskazówek użytkownika. Pamiętaj, że poprawki użytkownika (domeny, opisy) mają pierwszeństwo i nie są nadpisywane.

## Krok 4: Opis zmian

Przejrzyj zmiany, zaczynając od `risk: high` i `med`. Dla istotnych przeczytaj szczegóły `atlas_get_object`. Ryzyko oceniaj tak:
- **high**: coś przestanie działać albo grozi utrata danych (np. `brokenBy` niepuste: obiekty nadal używają usuniętej kolumny lub tabeli; usunięcie kolumny z danymi),
- **med**: wymaga uwagi (dynamiczny SQL, usunięte parametry, zmiana typu kolumny, usunięty obiekt z zależnymi),
- **low**: zmiana bezpieczna (nowy obiekt, indeks, zgodna wstecz zmiana parametrów).

Zmiany wynikają z porównania stanu („było → jest”), a nie z commitów. W UI każdy zmieniony obiekt ma Twój opis i różnicę definicji; pod spodem jest lista commitów od poprzedniej analizy. Opisy commitów (pole `commits`) wykorzystaj jako kontekst, „dlaczego” coś się zmieniło. Zapisz `atlas_save_change_notes`:
- `summary`: 2–4 zdania dla całego zestawu: co się zmieniło biznesowo, co wymaga uwagi przed wdrożeniem,
- `items`: dla każdej zmiany jedno lub dwa zdania (co i jaki ma wpływ, z nazwami zależnych obiektów) oraz `risk`.

## Krok 5: Aktualizacja mapy

- Obiekty z `newObjectsInAutoDomains` przypisz do domen z opisem (`atlas_assign`). Gdy pasującej domeny brak, utwórz ją (`atlas_save_domains`).
- Obiektom, których rola istotnie się zmieniła, zaktualizuj opis.
- Nowe pozycje do wyjaśnienia: `atlas_issues`, potem `atlas_propose` dla dynamicznego SQL i nierozwiązanych odwołań.

## Krok 6: Podsumowanie

`atlas_open_ui` (`view: changes`). W czacie krótko: zakres (od–do, liczba commitów), liczby zmian, **najważniejsze ryzyka z nazwami obiektów**, co zaktualizowano na mapie, adres UI.
