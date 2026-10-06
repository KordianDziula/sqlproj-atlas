---
name: explore
description: Odpowiada na pytania o strukturę i zależności baz w projekcie SSDT na podstawie analizy Data Architect, np. „co korzysta z tabeli sales.Orders”, „co się zepsuje, jeśli usunę kolumnę”, „gdzie jest zapisywany status zamówienia”, „jakie procedury czytają z CRM”. Użyj w projektach z plikami .sqlproj, gdy pytanie dotyczy obiektów bazy, relacji lub wpływu zmian.
---

# Data Architect: pytania o bazę

1. `atlas_status`. Jeśli projekt nie był analizowany, zaproponuj `/data-architect:init`. Na proste pytanie możesz odpowiedzieć też z samego kodu SQL.
2. Znajdź obiekty: `atlas_list_objects` z `search` (fragment nazwy), ewentualnie filtrem `project`, `type`.
3. Szczegóły i relacje: `atlas_get_object` (kolumny, parametry, kto korzysta, z czego korzysta, pozycje do wyjaśnienia).
4. Wpływ zmiany: `atlas_impact` (zależne obiekty przechodnio). Pamiętaj, że dynamiczny SQL i systemy zewnętrzne mogą ukrywać zależności; sprawdź pozycje do wyjaśnienia obiektu.
5. Odpowiedz konkretnie: nazwy obiektów z bazą (`Baza.schemat.nazwa`), rodzaj relacji (odczyt, zapis, wywołanie, klucz obcy), zastrzeżenia o niepewnych zależnościach.
6. Jeśli pomoże to użytkownikowi, zaproponuj podgląd w UI: `atlas_open_ui` z `object` (rozłoży relacje obiektu na mapie).
