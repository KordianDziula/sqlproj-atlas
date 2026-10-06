---
name: setup
description: Sprawdza zależności Data Architect (uv ze środowiskiem Pythona, .NET SDK 8+, silnik analizy zbudowany na tym komputerze) i za zgodą użytkownika doinstalowuje brakujące oraz buduje silnik. Użyj po instalacji lub aktualizacji wtyczki, gdy narzędzia atlas nie działają, gdy atlas_status zgłasza brak silnika albo gdy hak startowy sesji podpowiada /data-architect:setup.
---

# Data Architect: sprawdzenie i instalacja zależności

Wtyczka potrzebuje:
- **uv** (menedżer Pythona od Astral). Pobiera właściwego Pythona i pakiety wtyczki (MCP SDK, FastAPI, SQLAlchemy…) do izolowanego środowiska `~/.data-architect/venv`, więc nie miesza w systemowym Pythonie.
- **.NET SDK 8 lub nowszy**. Wtyczka zawiera kod źródłowy silnika analizy (C#, Microsoft DacFx + ScriptDom), a nie gotowe pliki `.dll`. Silnik buduje się na tym komputerze do `~/.data-architect/engine/`.

Działa tak samo na Windows i macOS (także Linux). Komunikuj się po polsku. **Niczego nie instaluj bez wyraźnej zgody użytkownika.**

## Krok 1: Sprawdzenie

Uruchom narzędziem Bash (katalog wtyczki: `${CLAUDE_PLUGIN_ROOT}`):

```
"${CLAUDE_PLUGIN_ROOT}/bin/atlas" doctor
```

Interpretacja:
- JSON z `"engine": {"ok": true}`: wszystko działa. Powiedz to i zaproponuj `/data-architect:init`.
- „brak uv”: brakuje uv (krok 3a).
- „środowisko wtyczki nie jest przygotowane”: uv jest, trzeba przygotować środowisko (krok 3b).
- JSON z `"engine": {"ok": false}`:
  - `"sdk": null`: brakuje .NET SDK 8+ (krok 3c), potem budowa silnika (krok 3d),
  - `sdk` jest, a `engine.error` mówi, że silnik nie jest zbudowany: tylko krok 3d (np. po aktualizacji wtyczki ze zmienionym silnikiem),
  - inny błąd w `engine.error`: pokaż go użytkownikowi.

## Krok 2: Zgoda

Zapytaj narzędziem `AskUserQuestion` o zgodę na brakujące kroki. Napisz, co, skąd i dokąd:
- **uv**: oficjalny instalator Astral (https://astral.sh/uv), ok. 15 MB, do `~/.local/bin`, bez uprawnień administratora.
- **Środowisko wtyczki**: uv pobiera z PyPI pakiety z `uv.lock` wtyczki (ok. 30 MB), a jeśli w systemie nie ma Pythona 3.11+, także Pythona (ok. 30 MB). Wszystko trafia do `~/.data-architect/venv` i pamięci podręcznej uv.
- **.NET SDK (LTS)**: oficjalny skrypt Microsoftu `dotnet-install`, ok. 250 MB, do `~/.dotnet`, bez uprawnień administratora.
- **Budowa silnika**: `dotnet publish` pobiera z NuGet pakiet Microsoft.SqlServer.DacFx (ok. 60 MB, do `~/.nuget/packages`) i kompiluje silnik do `~/.data-architect/engine/` (ok. 45 MB). Za pierwszym razem 1–3 minuty.

Opcje odpowiedzi: „Zainstaluj (Recommended)”, „Pokaż tylko polecenia, zainstaluję sam”. Przy drugiej podaj polecenia z kroku 3 i zakończ.

Sama budowa silnika (krok 3d), gdy reszta już jest, nic nie instaluje w systemie: wystarczy krótka informacja i zgoda.

## Krok 3: Instalacja (tylko po zgodzie)

**3a. uv**
- Windows: `powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"`
- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

**3b. Środowisko wtyczki** (także zaraz po instalacji uv; launcher sam znajdzie uv w `~/.local/bin`):
```
"${CLAUDE_PLUGIN_ROOT}/bin/atlas" install
```

**3c. .NET SDK** do `~/.dotnet` (wtyczka szuka go tam automatycznie):
- Windows:
  ```
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest https://dot.net/v1/dotnet-install.ps1 -OutFile $env:TEMP\dotnet-install.ps1; & $env:TEMP\dotnet-install.ps1 -Channel LTS -InstallDir $env:USERPROFILE\.dotnet"
  ```
- macOS / Linux:
  ```
  curl -sSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel LTS --install-dir "$HOME/.dotnet"
  ```

Jeśli użytkownik ma już .NET SDK 8+ zainstalowane inaczej (instalator Microsoftu, Homebrew, Visual Studio), `doctor` je znajdzie i ten krok można pominąć.

**3d. Budowa silnika**:
```
"${CLAUDE_PLUGIN_ROOT}/bin/atlas" build-engine
```
Wynik: „Silnik gotowy: …”. Przy błędzie polecenie wypisuje końcówkę komunikatów kompilatora: pokaż ją użytkownikowi (najczęstsza przyczyna to brak dostępu do NuGet, np. przez proxy firmowe).

Jeśli polecenie się nie powiedzie, pokaż użytkownikowi błąd i polecenie do samodzielnego uruchomienia. Nie obchodź błędów uprawnień.

## Krok 4: Weryfikacja

1. Ponownie uruchom `"${CLAUDE_PLUGIN_ROOT}/bin/atlas" doctor`. Ma zwrócić `"engine": {"ok": true}`.
2. Jeśli serwer MCP `atlas` nie działał (narzędzia `atlas_*` były niedostępne), poproś o ponowne połączenie: `/mcp` → atlas → Reconnect albo restart Claude Code.
3. Zaproponuj `/data-architect:init`.

Uwaga dla Windows: w aplikacji Claude desktop nie instaluj niczego ręcznie do `%LOCALAPPDATA%`. Aplikacja wirtualizuje AppData i inne programy (np. Claude Code w terminalu) nie zobaczą takiej instalacji. Katalogi `~/.local/bin`, `~/.dotnet` i `~/.data-architect` są bezpieczne.

Uwaga dla macOS: na launcherze `bin/atlas` musi być prawo wykonywania. Jeśli Bash zgłasza „Permission denied”, uruchom `chmod +x "${CLAUDE_PLUGIN_ROOT}/bin/atlas"`.
