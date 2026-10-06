---
name: open
description: Otwiera lokalną aplikację SSDT Atlas w przeglądarce (mapa tabel i procedur baz SSDT, zmiany od ostatniej analizy, pozycje do wyjaśnienia, wskazówki). Użyj, gdy użytkownik chce zobaczyć mapę, graf lub UI atlasu.
argument-hint: "[map|changes|issues|guides] [obiekt]"
---

# SSDT Atlas: otwarcie aplikacji

Argumenty: $ARGUMENTS

1. Ustal widok z argumentów lub prośby użytkownika: `map` (domyślnie), `changes` (zmiany), `issues` (do wyjaśnienia), `guides` (wskazówki). Jeśli podano nazwę obiektu (np. `sales.Orders`), przekaż ją jako `object`.
2. Wywołaj narzędzie `atlas_open_ui` (serwer MCP `atlas`).
3. Jeśli projekt nie był analizowany (UI pokaże pusty stan), zaproponuj `/ssdt-atlas:init`.
4. Podaj użytkownikowi adres i jednym zdaniem przypomnij, że aplikacja działa lokalnie, dopóki trwa sesja Claude Code (bez Claude'a: `<wtyczka>/bin/atlas ui`).
