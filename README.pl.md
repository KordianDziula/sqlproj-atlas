# SqlProj Atlas

Wtyczka Claude Code **SqlProj Atlas** do analizy projektów bazodanowych **SSDT (SQL Server)**.
English version, including what the plugin installs and runs and how it handles data: [README.md](README.md).

- Silnik Microsoft **DacFx + ScriptDom** wyciąga fakty: obiekty, kolumny, relacje, nierozwiązane odwołania.
- Claude grupuje obiekty w domeny biznesowe i je opisuje.
- Lokalna aplikacja webowa pokazuje mapę baz, zmiany „było → jest” od ostatniej analizy i pozycje do wyjaśnienia.
  Każdą wartość możesz w niej poprawić, a Twoje poprawki mają pierwszeństwo.

## Wymagania

- Claude Code i git
- projekt SSDT (pliki `.sqlproj`, format klasyczny lub SDK-style), najlepiej w repozytorium git
- **uv** i **.NET SDK 8+**: nie instaluj ręcznie, `/sqlproj-atlas:setup` doinstaluje je za Twoją zgodą
  do katalogu domowego (bez uprawnień administratora)
- Windows albo macOS (działa też na Linuksie)

Repozytorium zawiera tylko kod źródłowy. Silnik analizy (C#) buduje się na Twoim komputerze przy
`/sqlproj-atlas:setup` do `~/.sqlproj-atlas/engine/` (pakiety Microsoftu pobierane z NuGet, 1–3 minuty za pierwszym razem).

## Instalacja

```bash
claude plugin marketplace add KordianDziula/sqlproj-atlas
```

```bash
claude plugin install sqlproj-atlas@sqlproj-atlas
```

Potem uruchom Claude Code w katalogu projektu SSDT i wpisz `/sqlproj-atlas:setup`.

Aktualizacja:

```bash
claude plugin marketplace update sqlproj-atlas
```

```bash
claude plugin update sqlproj-atlas@sqlproj-atlas
```

## Użycie

| Komenda | Co robi |
|---|---|
| `/sqlproj-atlas:setup` | sprawdza i za zgodą doinstalowuje uv oraz .NET SDK, buduje silnik analizy |
| `/sqlproj-atlas:init` | pierwsza analiza: krótki wywiad, analiza silnikiem, domeny i opisy, otwarcie mapy |
| `/sqlproj-atlas:refresh` | ponowna analiza i porównanie z poprzednią: co się zmieniło, wpływ i ryzyko |
| `/sqlproj-atlas:open [widok]` | otwiera aplikację: `map`, `changes`, `issues`, `guides` |
| `/sqlproj-atlas:close` | zamyka aplikację (serwer w tle); i tak kończy się razem z sesją Claude Code |
| pytania w czacie | np. „co korzysta z tabeli sales.Orders?”, „co się zepsuje, jeśli usunę kolumnę X?” |

Przy pierwszej analizie Claude zawsze pyta, które bazy i schematy pominąć. Ustawienie trafia do `config.json`
(`excludeProjects`, `excludeSchemas`: `schemat` we wszystkich bazach albo `Baza.schemat` w jednej), a na mapie
pominięte bazy i schematy są widoczne jako szare kafelki.

Dane wtyczki trafiają do `<projekt>/.claude/sqlproj-atlas/` (katalog powstaje przy pierwszej analizie; dane z
wcześniejszych wersji, `.claude/ssdt-atlas` i `.claude/data-architect`, są przenoszone automatycznie).
`guidelines.md`, `overrides.json` i `config.json` warto trzymać w repozytorium projektu. Baza i pliki robocze
są wykluczone przez `.gitignore`.

## Prywatność

- Bez telemetrii: wtyczka nie wysyła kodu, schematu ani innych danych na żaden serwer. Jedyny ruch sieciowy to
  pobranie narzędzi przy `/sqlproj-atlas:setup`, za Twoją zgodą (uv, PyPI, .NET, NuGet).
- Dane osobowe: z historii git wtyczka czyta **autora, datę i opis commitów** (commit każdej analizy i commity między
  analizami). Zapisuje je w `atlas.db`, pokazuje w zakładce „Zmiany” i przekazuje Claude'owi jako kontekst do opisu
  zmian. Adresów e-mail nie czyta ani nie zapisuje.
- Usunięcie danych: skasuj katalog `.claude/sqlproj-atlas/` w projekcie (oraz `~/.sqlproj-atlas/` ze środowiskiem
  i silnikiem wtyczki).

## Problemy

- **Brak narzędzi `atlas_*`:** uruchom `/sqlproj-atlas:setup`, potem `/mcp` → atlas → Reconnect.
- **„Silnik analizy nie jest jeszcze zbudowany”** (np. po aktualizacji wtyczki): `/sqlproj-atlas:setup`.
- **macOS, „Permission denied” dla `scripts/atlas`:** `chmod +x <katalog-wtyczki>/scripts/atlas`.
- **Diagnostyka:** `<katalog-wtyczki>/scripts/atlas doctor`.
- **Log:** `.claude/sqlproj-atlas/atlas.log` w katalogu projektu.

## Licencja

[MIT](LICENSE). SqlProj Atlas to niezależny projekt, niepowiązany z Microsoftem. SQL Server, SQL Server
Data Tools (SSDT) i DacFx to znaki towarowe Microsoft Corporation, użyte tylko do opisu, z czym współpracuje wtyczka.
