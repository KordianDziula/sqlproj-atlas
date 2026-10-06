"""Uruchamianie lokalnej aplikacji webowej (uvicorn) i otwieranie przeglądarki.

W trybie MCP serwer HTTP działa w wątku tła procesu Claude Code (żyje tyle, co sesja).
Adres zapisujemy w .claude/data-architect/ui.lock: druga sesja w tym samym projekcie korzysta z działającej aplikacji.
"""

import json
import logging
import os
import threading
import time
import webbrowser
from urllib.parse import quote

import httpx
import uvicorn

from data_architect.core.project_files import now_iso
from data_architect.core.workspace import Workspace
from data_architect.web_app.app import create_app

log = logging.getLogger(__name__)


def ensure_ui(ws: Workspace) -> str:
    """Adres aplikacji: działającej dla tego projektu albo uruchomionej teraz w wątku tła."""
    if ws.ui_url:
        return ws.ui_url

    lock_file = ws.data_dir / "ui.lock"
    running = _running_instance(lock_file, ws)
    if running:
        ws.ui_url = running
        return running

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(ws), host="127.0.0.1", port=int(os.environ.get("ATLAS_PORT") or 0), log_level="warning"
        )
    )
    threading.Thread(target=server.run, name="atlas-ui", daemon=True).start()

    # czekamy, aż serwer zacznie nasłuchiwać, i odczytujemy przydzielony port
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("Aplikacja webowa nie uruchomiła się w 15 s")
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]

    ws.ui_url = f"http://127.0.0.1:{port}/"
    ws.ensure_data_dir()
    lock_file.write_text(
        json.dumps({"url": ws.ui_url, "port": port, "pid": os.getpid(), "startedAt": now_iso()}), encoding="utf-8"
    )
    log.info("UI: %s", ws.ui_url)
    return ws.ui_url


def open_ui(ws: Workspace, view: str | None = None, object_id: str | None = None) -> str:
    """Uruchamia UI (jeśli trzeba) i otwiera przeglądarkę na widoku, np. #map/Sales%7Csales.orders."""
    url = ensure_ui(ws)
    if view or object_id:
        url += "#" + (view or "map")
        if object_id:
            url += "/" + quote(object_id, safe="")

    open_browser(url)
    return url


def open_browser(url: str) -> None:
    if os.environ.get("ATLAS_NO_BROWSER") == "1":
        return
    try:
        webbrowser.open(url)
    except webbrowser.Error:
        log.warning("Nie udało się otworzyć przeglądarki: %s", url)


def serve_forever(ws: Workspace, open_in_browser: bool = True) -> None:
    """Tryb samodzielny (`bin/atlas ui`): uruchamia aplikację i czeka do Ctrl+C."""
    url = ensure_ui(ws)
    print(f"SSDT Atlas UI: {url}  (Ctrl+C aby zakończyć)", flush=True)
    if open_in_browser:
        open_browser(url)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def _running_instance(lock_file, ws: Workspace) -> str | None:
    """Adres z ui.lock, jeśli pod nim odpowiada aplikacja tego samego projektu."""
    try:
        url = json.loads(lock_file.read_text(encoding="utf-8"))["url"]
        ping = httpx.get(url + "api/ping", timeout=1.5).json()
    except (OSError, ValueError, KeyError, httpx.HTTPError):
        return None

    same_project = os.path.normcase(os.path.abspath(ping.get("project", ""))) == os.path.normcase(
        str(ws.project_dir.resolve())
    )
    return url if ping.get("ok") and same_project else None
