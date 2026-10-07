---
name: init
description: First run of SqlProj Atlas in an SSDT database project (SQL Server, .sqlproj files). Collects guidance from the user, analyzes every database with the Microsoft DacFx engine, aggregates the architecture into business domains with descriptions and opens the map in the browser. Use when the user wants to initialize, analyze or map an SSDT project for the first time.
argument-hint: "[additional guidance]"
---

# SqlProj Atlas: first analysis of a project

You are responsible for **aggregating the architecture**: the Microsoft engine (DacFx + ScriptDom) provides the facts (objects, columns, relationships, unresolved references), and you turn them into a readable map: business domains, descriptions, explanations. The user can later correct everything in the UI, and their corrections take precedence.

Tools of the `atlas` MCP server (full names start with `mcp__plugin_sqlproj-atlas_atlas__`): `atlas_status`, `atlas_analyze`, `atlas_configure`, `atlas_guidelines`, `atlas_list_objects`, `atlas_get_object`, `atlas_save_domains`, `atlas_assign`, `atlas_describe`, `atlas_issues`, `atlas_propose`, `atlas_open_ui`.

Additional guidance from the user (may be empty): $ARGUMENTS

Talk to the user in the language they use (for example Polish if they write in Polish). Work in batches and report progress briefly ("Analyzing…", "Aggregating domains in the Sales database…").

## Step 1: Status

Call `atlas_status`.
- The `atlas` tools are unavailable (the server did not start, usually uv is missing or the environment is not prepared) or `engine.ok = false` (usually .NET SDK 8+ is missing or the engine has not been built on this computer yet) → follow the steps of the `/sqlproj-atlas:setup` skill (check, user consent, installation). Continue the initialization only once the engine works.
- `sqlProjectsFound` is empty → this is not an SSDT project (no .sqlproj files). Say so and stop.
- `initialized = true` and `guidelines = true` → the project has already been analyzed. Suggest `/sqlproj-atlas:refresh` (changes since the last analysis). Continue the full initialization only if the user explicitly wants it.

## Step 2: Engine analysis

Call `atlas_analyze`. From the summary read: projects (databases) and their formats, schemas with sample names, name prefixes (e.g. `usp_`, `vw_`, `stg_`), SQLCMD variables and references between databases, external systems (linked servers, databases outside the solution), items to clarify.

## Step 3: Interview (interpretation guidelines)

Ask questions with the `AskUserQuestion` tool (at most 4 questions per call, so at most 2 calls). **Every question has ready-made answer suggestions** inferred from the summary, so the user mostly confirms. Scope:
1. **What to exclude (always ask, even when nothing looks redundant)**:
   - **databases**: which projects to exclude (e.g. test or sample projects),
   - **schemas**: which schemas to exclude, in all databases or in one (e.g. temporary, technical or backup ones: `tmp`, `test`, `bak`, `old`). Take the suggestions from the list of schemas in the summary; one of the answers is always "Don't exclude anything".
   Also: how data flows between databases (e.g. Sales → Staging → DWH, based on the references).
2. **Domains**: is a schema a business domain? How should the `dbo` schema be split (e.g. by prefixes)?
3. **Naming conventions**: what the detected prefixes and suffixes mean (e.g. `_old`, `_bak` = to be removed).
4. **External systems**: what the detected linked servers and SQLCMD variables without a project are.
5. **Glossary and style**: business abbreviations, language of the descriptions (by default the user's language), anything to leave out.

If `AskUserQuestion` is unavailable or the session is non-interactive, don't wait: make reasonable assumptions based on the analysis and record them as assumptions to confirm.

Save the guidelines with `atlas_guidelines` (`action: save`) as Markdown with `## Title` sections, e.g. "Databases in the solution", "Domain split", "Naming conventions", "What we exclude", "External systems", "Glossary", "Description style". Content: specific, short sentences, in the user's language.

If the user wants to exclude something: `atlas_configure` with `excludeProjects` (project names) and/or `excludeSchemas` (`schema` in all databases or `Project.schema` in one), then `atlas_analyze` again. Record it in the guidelines in the "What we exclude" section too.

## Step 4: Architecture aggregation (the most important part)

For **each database (project)** separately:
1. Fetch the objects: `atlas_list_objects` with `project`, `limit: 150`, following batches via `offset` until the end.
2. Design **business domains** (usually 3–12 per database): areas of responsibility recognizable to the business (e.g. "Orders", "Customers", "Payments", "Reference data and configuration"). Follow the user's guidelines, the schemas, foreign-key clusters (tables linked by FKs usually belong to one domain), which tables the procedures use, and the names. Avoid single-object domains and an "Other" bucket unless there is really no other way.
3. Save the domains: `atlas_save_domains` (name + 1–2 sentence description from the business perspective, `order` following the data flow or importance).
4. Assign **all** objects: `atlas_assign` in batches of at most 100 items. Add a **description** to each object (1–2 sentences: what it does for the business, without repeating the object name). In very large databases (over 400 objects) describe at least all tables and the most connected objects, and only assign a domain to the rest.
5. Check `stillInAutoDomainsByProject` in the `atlas_assign` response: there must be no entry for the current database (0 objects without a domain). If there are any, assign the missing ones (`atlas_list_objects` with `project` and `unassigned: true`).

The `userLocked` field marks the user's corrections. Don't try to change them.

## Step 5: Descriptions of databases and external systems

`atlas_describe`: for each database (`project:Name`) 1–2 sentences about its role; for each external system (`ext|name`) a careful description based on its usage and the guidelines. The user confirms the system description in the UI.

## Step 6: Items to clarify

`atlas_issues` (open). For dynamic SQL and unresolved references read the code (`atlas_get_object`) and propose an explanation with `atlas_propose`:
- `text`: what this fragment does and what it refers to,
- `targets`: the objects it probably concerns (id + kind `reads`/`writes`/`calls`), only when you have grounds for it,
- `confidence`: an honest confidence of 0–100.
For external systems the description from step 5 is enough.

## Step 7: UI and summary

Call `atlas_open_ui` (`view: map`) and give the address. Finally, briefly in the chat:
- how many databases, domains and objects,
- which domains were created (a few per database),
- how many items are waiting to be clarified and where to find them (the "Do wyjaśnienia" tab, i.e. items to clarify),
- what next: `/sqlproj-atlas:refresh` after new commits.
