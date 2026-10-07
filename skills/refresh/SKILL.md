---
name: refresh
description: Refreshes the SqlProj Atlas analysis. Compares the current state of the SSDT project with the previous analysis ("before → after"), describes the changes and their impact (e.g. a dropped column still used in procedures), assigns new objects to domains and opens the Changes view. Use when the user asks what changed in the databases or wants to update the map.
---

# SqlProj Atlas: refresh and describe changes

Talk to the user in the language they use. Tools of the `atlas` MCP server (full names start with `mcp__plugin_sqlproj-atlas_atlas__`).

## Step 1: Status

`atlas_status`.
- `initialized = false` → ask for `/sqlproj-atlas:init` and stop.
- `engine.ok = false` → pass on the error message, suggest `/sqlproj-atlas:setup` (e.g. after a plugin update the engine has to be built again) and stop.

## Step 2: Changes

- If `newCommitsSinceAnalysis > 0` or `uncommittedChanges = true` → `atlas_refresh` (new analysis and comparison).
- Otherwise check the latest change set: `atlas_changes`. If it has changes without a summary (e.g. a refresh interrupted in a previous session), describe it. If everything is already described, say that nothing has changed since the last analysis and offer to open the UI.

## Step 3: Context

`atlas_guidelines` (`get`), to follow the user's guidelines. Remember that the user's corrections (domains, descriptions) take precedence and are never overwritten.

## Step 4: Describe the changes

Go through the changes, starting with `risk: high` and `med`. For the important ones read the details with `atlas_get_object`. Assess risk like this:
- **high**: something will stop working or data may be lost (e.g. `brokenBy` is not empty: objects still use a dropped column or table; dropping a column that holds data),
- **med**: needs attention (dynamic SQL, removed parameters, a changed column type, a removed object with dependents),
- **low**: a safe change (a new object, an index, a backward-compatible parameter change).

Changes come from comparing states ("before → after"), not from commits. In the UI every changed object shows your description and the definition diff; below it there is the list of commits since the previous analysis. Use the commit messages (the `commits` field) as context for "why" something changed. Save with `atlas_save_change_notes`, in the user's language:
- `summary`: 2–4 sentences for the whole change set: what changed for the business, what needs attention before deployment,
- `items`: one or two sentences per change (what changed and its impact, naming the dependent objects) and `risk`.

## Step 5: Update the map

- Assign the objects from `newObjectsInAutoDomains` to domains with a description (`atlas_assign`). When no domain fits, create one (`atlas_save_domains`).
- Update the description of objects whose role changed significantly.
- New items to clarify: `atlas_issues`, then `atlas_propose` for dynamic SQL and unresolved references.

## Step 6: Summary

`atlas_open_ui` (`view: changes`). Briefly in the chat: the range (from–to, number of commits), the number of changes, **the main risks with object names**, what was updated on the map, the UI address.
