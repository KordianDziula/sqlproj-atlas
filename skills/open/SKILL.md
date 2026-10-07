---
name: open
description: Opens the local SqlProj Atlas app in the browser (map of the tables and procedures of the SSDT databases, changes since the last analysis, items to clarify, guidelines). Use when the user wants to see the map, graph or UI of the atlas.
argument-hint: "[map|changes|issues|guides] [object]"
---

# SqlProj Atlas: open the app

Arguments: $ARGUMENTS

1. Determine the view from the arguments or the user's request: `map` (default), `changes`, `issues` (items to clarify), `guides` (guidelines). If an object name was given (e.g. `sales.Orders`), pass it as `object`.
2. Call the `atlas_open_ui` tool (MCP server `atlas`).
3. If the project has not been analyzed (the UI shows an empty state), suggest `/sqlproj-atlas:init`.
4. Give the user the address and remind them in one sentence, in their language, that the app runs locally until the end of the Claude Code session or until `/sqlproj-atlas:close` (without Claude: `<plugin>/scripts/atlas ui`).
