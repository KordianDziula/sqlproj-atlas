# SqlProj Atlas

A Claude Code plugin that maps and explains **SQL Server Data Tools (SSDT)** database projects (`.sqlproj`).
Polska wersja: [README.pl.md](README.pl.md).

- **Facts come from Microsoft's own tooling.** A small engine built on **DacFx** (the library SSDT itself uses) and
  **ScriptDom** (the T-SQL parser) extracts objects, columns, foreign keys, reads and writes, procedure calls,
  cross-database references and unresolved references. Nothing structural is guessed by the model.
- **Claude aggregates the architecture.** It groups objects into business domains, writes short descriptions of
  objects, databases and external systems, and proposes explanations for dynamic SQL.
- **You have the last word.** A local web app shows the map, the changes since the last analysis and the items to
  clarify. You can correct any value there; your corrections are stored in `overrides.json` and always take
  precedence over Claude's.

## What you can do with it

- See every database of a solution on one map: tables and procedures grouped by business domain and schema, with
  relationships expanded when you click an object.
- Ask questions in the chat, for example:
  - "What uses the `sales.Orders` table?"
  - "What breaks if I drop the `DiscountCode` column?"
  - "Where is the order status written?"
  - "Which procedures read from the CRM linked server?"
- After new commits, run `/sqlproj-atlas:refresh` to get a "before → after" comparison of every changed object with a
  definition diff, an impact list (objects that will stop working) and a risk rating, plus the list of commits for
  context.
- Exclude databases or schemas you don't care about (test projects, `tmp`, `bak`, ...). Excluded ones are shown as
  grey tiles on the map.

## Requirements

- Claude Code, and git if you want change tracking between analyses
- An SSDT project (classic or SDK-style `.sqlproj`)
- Windows or macOS (Linux works too)
- **uv** and **.NET SDK 8+**. You don't need to install them yourself: `/sqlproj-atlas:setup` checks them and, only
  after you agree, installs what is missing into your home directory (no administrator rights).

## Installation

```bash
claude plugin marketplace add KordianDziula/sqlproj-atlas
```

```bash
claude plugin install sqlproj-atlas@sqlproj-atlas
```

Then start Claude Code in the folder of your SSDT project and run `/sqlproj-atlas:setup`, followed by `/sqlproj-atlas:init`.

## Commands

| Command | What it does |
|---|---|
| `/sqlproj-atlas:setup` | checks uv, the Python environment and .NET SDK; with your consent installs what is missing and builds the engine |
| `/sqlproj-atlas:init` | first analysis: a short interview (including which databases and schemas to exclude), engine analysis, domains and descriptions, opens the map |
| `/sqlproj-atlas:refresh` | analyzes the project again and compares it with the previous analysis: what changed, impact and risk |
| `/sqlproj-atlas:open [view]` | opens the local app: `map`, `changes`, `issues`, `guides` |
| `/sqlproj-atlas:close` | stops the local app (it also stops by itself when the Claude Code session ends) |

Claude answers in your language. The web app and the tool messages are currently in Polish.

## What the plugin installs, downloads and runs

Everything below happens on your computer and is visible in the source code of this repository.

- **Session start hook** (`hooks/hooks.json`): runs `scripts/atlas check`. In a folder with an SSDT project it
  checks that the engine works and, if not, suggests `/sqlproj-atlas:setup`. Elsewhere it prints nothing, unless uv or
  the plugin environment is missing, in which case it suggests `/sqlproj-atlas:setup` too.
- **Local MCP server** (`.mcp.json`): `scripts/atlas` (a shell launcher, `scripts/atlas.cmd` on Windows) runs the
  plugin's Python code from `src/` with `uv run --frozen`, using the exact package versions pinned in `uv.lock`
  (MCP SDK, FastAPI, Uvicorn, SQLAlchemy, Pydantic, Typer, GitPython, wcmatch, httpx).
- **Installation by `/sqlproj-atlas:setup`, only after you confirm it:**
  - uv, with Astral's official installer (`https://astral.sh/uv`), into `~/.local/bin`,
  - the plugin's Python environment (`uv sync` from `uv.lock`, packages from PyPI) into `~/.sqlproj-atlas/venv`,
  - .NET SDK (LTS), with Microsoft's official `dotnet-install` script (`https://dot.net/v1/dotnet-install.*`),
    into `~/.dotnet`,
  - the analysis engine: `dotnet publish` compiles the C# sources in `engine/` (they depend on the
    `Microsoft.SqlServer.DacFx` NuGet package) into `~/.sqlproj-atlas/engine/<hash of the sources>`.
  The repository contains no prebuilt binaries.
- **Analysis**: the engine reads the `.sql` files of your projects and runs locally as `dotnet atlas-engine.dll`.
- **Local web app**: an HTTP server on `127.0.0.1` (random port, or `ATLAS_PORT`) inside the MCP server process.
  It is started only when you open the app, listens only on `127.0.0.1` and rejects requests with any other `Host`
  header. The app can't
  start an analysis; it only shows results and saves your corrections.

## Data and privacy

- No telemetry. The plugin doesn't send your code, your schema or anything else to any server. The only network
  traffic is the downloads listed above, made during setup with your consent (uv, PyPI, .NET, NuGet).
- Results are stored in your project in `.claude/sqlproj-atlas/`. The folder is created on the first analysis,
  correction or opening of the app, never just by starting a session:
  - `guidelines.md`, `overrides.json`, `config.json`: your guidelines, corrections and settings, worth committing,
  - `atlas.db` (SQLite), `atlas.log`, `ui.lock`, `work/`: local files, excluded by a generated `.gitignore`.
- What Claude sees: the analysis results returned by the plugin's tools in your Claude Code session, under the same
  terms as the rest of the session.

## Platform support

The plugin is built for Claude Code (terminal, IDE extensions, desktop app Code tab). It relies on a local MCP
server and a session hook, so it is not useful in claude.ai chat.

## Troubleshooting

- **No `atlas_*` tools:** run `/sqlproj-atlas:setup`, then `/mcp` → atlas → Reconnect.
- **"Silnik analizy nie jest jeszcze zbudowany" (the engine is not built yet), e.g. after an update:** run
  `/sqlproj-atlas:setup`.
- **macOS, "Permission denied" for `scripts/atlas`:** `chmod +x <plugin folder>/scripts/atlas`.
- **Diagnostics:** `<plugin folder>/scripts/atlas doctor`.
- **Log:** `.claude/sqlproj-atlas/atlas.log` in your project.

## Support

Report problems and ask questions in the [issue tracker](https://github.com/KordianDziula/sqlproj-atlas/issues), or write to
[kordian.dziula@wp.pl](mailto:kordian.dziula@wp.pl). Please report security issues by email.

## Development

```bash
uv sync
```

```bash
uv run playwright install chromium
```

```bash
uv run pytest --cov
```

The test suite runs the real engine on a generated sample solution, the MCP server over stdio and the web app in a
real browser. `uv run pytest -m "not ui"` skips the browser tests.

## License

[MIT](LICENSE)

SqlProj Atlas is an independent project. It is not affiliated with, sponsored or endorsed by Microsoft.
SQL Server, SQL Server Data Tools (SSDT) and DacFx are trademarks of Microsoft Corporation and are mentioned only to
describe what the plugin works with.
