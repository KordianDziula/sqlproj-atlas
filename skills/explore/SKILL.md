---
name: explore
description: Answers questions about the structure and dependencies of the databases in an SSDT project based on the SqlProj Atlas analysis, e.g. "what uses the sales.Orders table", "what breaks if I drop this column", "where is the order status written", "which procedures read from the CRM". Use in projects with .sqlproj files when the question is about database objects, relationships or the impact of changes.
---

# SqlProj Atlas: questions about the database

Answer in the language the user uses.

1. `atlas_status`. If the project has not been analyzed, suggest `/sqlproj-atlas:init`. A simple question can also be answered from the SQL code alone.
2. Find the objects: `atlas_list_objects` with `search` (part of the name), optionally filtered by `project` or `type`.
3. Details and relationships: `atlas_get_object` (columns, parameters, what uses it, what it uses, items to clarify).
4. Impact of a change: `atlas_impact` (transitive dependents). Remember that dynamic SQL and external systems can hide dependencies; check the object's items to clarify.
5. Answer specifically: object names with their database (`Database.schema.name`), the kind of relationship (read, write, call, foreign key), caveats about uncertain dependencies.
6. If it helps the user, offer a view in the UI: `atlas_open_ui` with `object` (it expands the object's relationships on the map).
