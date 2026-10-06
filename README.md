# claude-ssdt-atlas

Wtyczka Claude Code **SSDT Atlas** do analizy projektów bazodanowych **SSDT (SQL Server)**.

- Silnik Microsoft **DacFx + ScriptDom** wyciąga fakty: obiekty, kolumny, relacje, nierozwiązane odwołania.
- Claude grupuje obiekty w domeny biznesowe i je opisuje.
- Lokalna aplikacja webowa pokazuje mapę baz, zmiany „było → jest” od ostatniej analizy i pozycje do wyjaśnienia.
  Każdą wartość możesz w niej poprawić, a Twoje poprawki mają pierwszeństwo.

## Wymagania

- Claude Code i git
- projekt SSDT (pliki `.sqlproj`, format klasyczny lub SDK-style), najlepiej w repozytorium git
- **uv** i **.NET SDK 8+**: nie instaluj ręcznie, `/ssdt-atlas:setup` doinstaluje je za Twoją zgodą
  do katalogu domowego (bez uprawnień administratora)
- Windows albo macOS (działa też na Linuksie)

Repozytorium zawiera tylko kod źródłowy. Silnik analizy (C#) buduje się na Twoim komputerze przy
`/ssdt-atlas:setup` do `~/.ssdt-atlas/engine/` (pakiety Microsoftu pobierane z NuGet, 1–3 minuty za pierwszym razem).

## Instalacja

```bash
claude plugin marketplace add https://gitlab.com/kordiandziula/claude-ssdt-atlas.git
```

```bash
claude plugin install ssdt-atlas@claude-ssdt-atlas
```

Potem uruchom Claude Code w katalogu projektu SSDT i wpisz `/ssdt-atlas:setup`.

Aktualizacja:

```bash
claude plugin marketplace update claude-ssdt-atlas
```

```bash
claude plugin update ssdt-atlas@claude-ssdt-atlas
```

## Użycie

| Komenda | Co robi |
|---|---|
| `/ssdt-atlas:setup` | sprawdza i za zgodą doinstalowuje uv oraz .NET SDK, buduje silnik analizy |
| `/ssdt-atlas:init` | pierwsza analiza: krótki wywiad, analiza silnikiem, domeny i opisy, otwarcie mapy |
| `/ssdt-atlas:refresh` | ponowna analiza i porównanie z poprzednią: co się zmieniło, wpływ i ryzyko |
| `/ssdt-atlas:open [widok]` | otwiera aplikację: `map`, `changes`, `issues`, `guides` |
| `/ssdt-atlas:close` | zamyka aplikację (serwer w tle); i tak kończy się razem z sesją Claude Code |
| pytania w czacie | np. „co korzysta z tabeli sales.Orders?”, „co się zepsuje, jeśli usunę kolumnę X?” |

Przy pierwszej analizie Claude zawsze pyta, które bazy i schematy pominąć. Ustawienie trafia do `config.json`
(`excludeProjects`, `excludeSchemas`: `schemat` we wszystkich bazach albo `Baza.schemat` w jednej), a na mapie
pominięte bazy i schematy są widoczne jako szare kafelki.

Dane wtyczki trafiają do `<projekt>/.claude/ssdt-atlas/` (katalog powstaje przy pierwszej analizie; dane z
wcześniejszego `.claude/data-architect` są przenoszone automatycznie).
`guidelines.md`, `overrides.json` i `config.json` warto trzymać w repozytorium projektu. Baza i pliki robocze
są wykluczone przez `.gitignore`.

## Problemy

- **Brak narzędzi `atlas_*`:** uruchom `/ssdt-atlas:setup`, potem `/mcp` → atlas → Reconnect.
- **„Silnik analizy nie jest jeszcze zbudowany”** (np. po aktualizacji wtyczki): `/ssdt-atlas:setup`.
- **macOS, „Permission denied” dla `bin/atlas`:** `chmod +x <katalog-wtyczki>/bin/atlas`.
- **Diagnostyka:** `<katalog-wtyczki>/bin/atlas doctor`.
- **Log:** `.claude/ssdt-atlas/atlas.log` w katalogu projektu.
