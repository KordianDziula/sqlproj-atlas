---
name: setup
description: Checks the SqlProj Atlas dependencies (uv with the Python environment, .NET SDK 8+, the analysis engine built on this computer) and, with the user's consent, installs what is missing and builds the engine. Use after installing or updating the plugin, when the atlas tools don't work, when atlas_status reports a missing engine or when the session start hook suggests /sqlproj-atlas:setup.
---

# SqlProj Atlas: check and install dependencies

The plugin needs:
- **uv** (the Python manager from Astral). It fetches the right Python and the plugin's packages (MCP SDK, FastAPI, SQLAlchemy…) into an isolated environment `~/.sqlproj-atlas/venv`, so it doesn't touch the system Python.
- **.NET SDK 8 or newer**. The plugin ships the source code of the analysis engine (C#, Microsoft DacFx + ScriptDom), not prebuilt `.dll` files. The engine is built on this computer into `~/.sqlproj-atlas/engine/`.

It works the same on Windows and macOS (and Linux). Talk to the user in the language they use. **Never install anything without the user's explicit consent.**

## Step 1: Check

Run with the Bash tool (plugin directory: `${CLAUDE_PLUGIN_ROOT}`):

```
"${CLAUDE_PLUGIN_ROOT}/scripts/atlas" doctor
```

How to read it (the launcher messages are in Polish):
- JSON with `"engine": {"ok": true}`: everything works. Say so and suggest `/sqlproj-atlas:init`.
- "brak uv" (uv is missing): step 3a.
- "środowisko wtyczki nie jest przygotowane" (the plugin environment is not prepared): uv is there, the environment has to be prepared (step 3b).
- JSON with `"engine": {"ok": false}`:
  - `"sdk": null`: .NET SDK 8+ is missing (step 3c), then build the engine (step 3d),
  - `sdk` is present and `engine.error` says the engine is not built: only step 3d (e.g. after a plugin update with a changed engine),
  - another error in `engine.error`: show it to the user.

## Step 2: Consent

Ask for consent to the missing steps with the `AskUserQuestion` tool. Say what, from where and to where:
- **uv**: the official Astral installer (https://astral.sh/uv), about 15 MB, into `~/.local/bin`, no administrator rights.
- **Plugin environment**: uv downloads the packages from the plugin's `uv.lock` from PyPI (about 30 MB) and, if the system has no Python 3.12+, Python as well (about 30 MB). Everything goes to `~/.sqlproj-atlas/venv` and the uv cache.
- **.NET SDK (LTS)**: Microsoft's official `dotnet-install` script, about 250 MB, into `~/.dotnet`, no administrator rights.
- **Engine build**: `dotnet publish` downloads the Microsoft.SqlServer.DacFx package from NuGet (about 60 MB, into `~/.nuget/packages`) and compiles the engine into `~/.sqlproj-atlas/engine/` (about 45 MB). 1–3 minutes the first time.

Answer options: "Install (Recommended)", "Only show the commands, I'll install myself". For the second one give the commands from step 3 and stop.

Building the engine alone (step 3d), when everything else is in place, installs nothing in the system: a short note and consent are enough.

## Step 3: Installation (only after consent)

**3a. uv**
- Windows: `powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"`
- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

**3b. Plugin environment** (also right after installing uv; the launcher finds uv in `~/.local/bin` by itself):
```
"${CLAUDE_PLUGIN_ROOT}/scripts/atlas" install
```

**3c. .NET SDK** into `~/.dotnet` (the plugin looks for it there automatically):
- Windows:
  ```
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest https://dot.net/v1/dotnet-install.ps1 -OutFile $env:TEMP\dotnet-install.ps1; & $env:TEMP\dotnet-install.ps1 -Channel LTS -InstallDir $env:USERPROFILE\.dotnet"
  ```
- macOS / Linux:
  ```
  curl -sSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel LTS --install-dir "$HOME/.dotnet"
  ```

If the user already has .NET SDK 8+ installed another way (Microsoft installer, Homebrew, Visual Studio), `doctor` finds it and this step can be skipped.

**3d. Engine build**:
```
"${CLAUDE_PLUGIN_ROOT}/scripts/atlas" build-engine
```
Result: "Silnik gotowy: …" (engine ready). On an error the command prints the tail of the compiler output: show it to the user (the most common cause is no access to NuGet, e.g. because of a corporate proxy).

If a command fails, show the user the error and the command to run themselves. Don't work around permission errors.

## Step 4: Verification

1. Run `"${CLAUDE_PLUGIN_ROOT}/scripts/atlas" doctor` again. It must return `"engine": {"ok": true}`.
2. If the `atlas` MCP server was not running (the `atlas_*` tools were unavailable), ask the user to reconnect: `/mcp` → atlas → Reconnect, or restart Claude Code.
3. Suggest `/sqlproj-atlas:init`.

Note for Windows: in the Claude desktop app don't install anything manually into `%LOCALAPPDATA%`. The app virtualizes AppData and other programs (e.g. Claude Code in a terminal) won't see such an installation. The `~/.local/bin`, `~/.dotnet` and `~/.sqlproj-atlas` directories are safe.

Note for macOS: the `scripts/atlas` launcher must be executable. If Bash reports "Permission denied", run `chmod +x "${CLAUDE_PLUGIN_ROOT}/scripts/atlas"`.
