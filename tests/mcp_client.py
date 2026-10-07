"""Synchroniczny klient MCP do testów: oficjalny ClientSession (SDK `mcp`) połączony z serwerem po stdio.

SDK jest asynchroniczne, a testy piszemy synchronicznie. Dlatego sesja działa w pętli asyncio w osobnym
wątku, w jednym długim zadaniu (konteksty anyio muszą być otwierane i zamykane w tym samym zadaniu),
a wywołania z testu trafiają do niej przez kolejkę.
"""

import asyncio
import json
import os
import sys
import threading
from concurrent.futures import Future
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SRC = Path(__file__).resolve().parents[1] / "src"  # kod wtyczki (nie jest instalowany jako pakiet)


class ToolCallError(Exception):
    """Narzędzie zwróciło błąd (isError); treść komunikatu jest w str(e)."""


class McpClient:
    def __init__(self, project_dir: Path, timeout: float = 600):
        self.timeout = timeout

        # SDK uruchamia serwer z okrojonym środowiskiem; konfigurację pomiaru pokrycia (pytest --cov)
        # przekazujemy jawnie
        env = {"ATLAS_PROJECT_DIR": str(project_dir), "ATLAS_NO_BROWSER": "1", "PYTHONPATH": str(SRC)}
        if "COVERAGE_PROCESS_CONFIG" in os.environ:
            env["COVERAGE_PROCESS_CONFIG"] = os.environ["COVERAGE_PROCESS_CONFIG"]

        self._params = StdioServerParameters(
            command=sys.executable, args=["-m", "sqlproj_atlas"], env=env, cwd=str(project_dir)
        )
        self._loop = asyncio.new_event_loop()
        self._jobs: asyncio.Queue | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._session_task = asyncio.run_coroutine_threadsafe(self._serve(), self._loop)
        if not self._ready.wait(60):
            raise TimeoutError("Serwer MCP nie odpowiedział na initialize w 60 s")
        if self._error:
            raise self._error

    # --- API dla testów ---

    def list_tools(self) -> list[str]:
        return [t.name for t in self._call(lambda s: s.list_tools()).tools]

    def call(self, name: str, arguments: dict | None = None):
        """Wywołuje narzędzie i zwraca wynik zdekodowany z JSON; błąd narzędzia → ToolCallError."""
        result = self._call(lambda s: s.call_tool(name, arguments or {}, read_timeout_seconds=self.timeout))
        text = result.content[0].text if result.content else ""
        if result.is_error:
            raise ToolCallError(text)
        return json.loads(text)

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._jobs.put_nowait, None)
        self._session_task.result(30)
        self._loop.call_soon_threadsafe(self._loop.stop)

    # --- wnętrze ---

    def _call(self, operation):
        future: Future = Future()
        self._loop.call_soon_threadsafe(self._jobs.put_nowait, (operation, future))
        return future.result(self.timeout)

    async def _serve(self) -> None:
        """Jedno zadanie na całą sesję: połączenie, obsługa kolejki wywołań, zamknięcie."""
        self._jobs = asyncio.Queue()
        try:
            async with stdio_client(self._params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                self._ready.set()

                while (job := await self._jobs.get()) is not None:
                    operation, future = job
                    try:
                        future.set_result(await operation(session))
                    except Exception as e:  # noqa: BLE001 — błąd wraca do wątku testu
                        future.set_exception(e)
        except BaseException as e:
            self._error = e
            self._ready.set()
            raise
