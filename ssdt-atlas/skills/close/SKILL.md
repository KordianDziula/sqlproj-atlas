---
name: close
description: Zamyka lokalną aplikację SSDT Atlas (dashboard w przeglądarce, serwer HTTP w tle). Użyj, gdy użytkownik chce wyłączyć UI, dashboard albo serwer aplikacji SSDT Atlas.
---

# SSDT Atlas: zamknięcie aplikacji

1. Wywołaj narzędzie `atlas_close_ui` (serwer MCP `atlas`).
2. Przekaż wynik jednym zdaniem:
   - `closed: true`: aplikacja pod podanym adresem została zamknięta; kartę w przeglądarce można zamknąć. Analiza i poprawki zostają, `/ssdt-atlas:open` uruchomi aplikację ponownie.
   - `closed: false`: podaj `reason` (np. aplikacja nie była uruchomiona albo działa w innej sesji Claude Code).

Aplikacja nie jest osobnym procesem: to wątek serwera MCP wtyczki, który i tak kończy się razem z sesją Claude Code. Tryb samodzielny (`<wtyczka>/bin/atlas ui`) zamyka się klawiszami Ctrl+C.
