---
name: close
description: Closes the local SqlProj Atlas app (the dashboard in the browser, the HTTP server in the background). Use when the user wants to stop the UI, the dashboard or the SqlProj Atlas app server.
---

# SqlProj Atlas: close the app

1. Call the `atlas_close_ui` tool (MCP server `atlas`).
2. Report the result in one sentence, in the user's language:
   - `closed: true`: the app at the given address has been closed; the browser tab can be closed. The analysis and corrections stay, and `/sqlproj-atlas:open` starts the app again.
   - `closed: false`: give the `reason` (e.g. the app was not running or it runs in another Claude Code session).

The app is not a separate process: it is a thread of the plugin's MCP server, which ends together with the Claude Code session anyway. The standalone mode (`<plugin>/scripts/atlas ui`) is stopped with Ctrl+C.
