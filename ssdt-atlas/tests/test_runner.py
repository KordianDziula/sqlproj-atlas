"""Uruchamianie aplikacji webowej (web_app/runner.py).

Uvicorn w wątku tła, ui.lock, port, przeglądarka, tryb samodzielny.
"""

import json
import socket
import webbrowser

import httpx
import pytest

from ssdt_atlas.core.workspace import Workspace
from ssdt_atlas.web_app import runner


def ping(url: str) -> dict:
    return httpx.get(url + "api/ping", timeout=5).json()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_ensure_ui_starts_server_and_writes_lock(workspace):
    url = runner.ensure_ui(workspace)

    assert url.startswith("http://127.0.0.1:")
    assert ping(url) == {"ok": True, "project": str(workspace.project_dir)}
    lock = json.loads((workspace.data_dir / "ui.lock").read_text(encoding="utf-8"))
    assert lock["url"] == url
    assert runner.ensure_ui(workspace) == url, "drugie wywołanie zwraca ten sam adres"


def test_second_session_reuses_running_ui(workspace):
    url = runner.ensure_ui(workspace)
    second = Workspace(workspace.project_dir)
    try:
        assert runner.ensure_ui(second) == url
    finally:
        second.close()


def test_stale_lock_starts_new_server(workspace):
    workspace.ensure_data_dir()
    (workspace.data_dir / "ui.lock").write_text(
        json.dumps({"url": f"http://127.0.0.1:{free_port()}/"}), encoding="utf-8"
    )
    url = runner.ensure_ui(workspace)
    assert ping(url)["ok"] is True


def test_lock_of_other_project_is_ignored(workspace, tmp_path):
    other = Workspace(tmp_path / "inny")
    try:
        other_url = runner.ensure_ui(other)
        workspace.ensure_data_dir()
        (workspace.data_dir / "ui.lock").write_text(json.dumps({"url": other_url}), encoding="utf-8")
        assert runner.ensure_ui(workspace) != other_url
    finally:
        other.close()


def test_preferred_port(workspace, monkeypatch):
    port = free_port()
    monkeypatch.setenv("ATLAS_PORT", str(port))
    assert runner.ensure_ui(workspace) == f"http://127.0.0.1:{port}/"


@pytest.mark.parametrize(
    ("view", "object_id", "suffix"),
    [(None, None, "/"), ("issues", None, "/#issues"), (None, "Sales|sales.orders", "/#map/Sales%7Csales.orders")],
)
def test_open_ui_address(workspace, monkeypatch, view, object_id, suffix):
    opened = []
    monkeypatch.setattr(runner, "open_browser", opened.append)
    url = runner.open_ui(workspace, view, object_id)
    assert url.endswith(suffix)
    assert opened == [url]


def test_open_browser_respects_setting(monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)

    monkeypatch.setenv("ATLAS_NO_BROWSER", "1")
    runner.open_browser("http://127.0.0.1:1/")
    assert opened == []

    monkeypatch.delenv("ATLAS_NO_BROWSER")
    runner.open_browser("http://127.0.0.1:1/")
    assert opened == ["http://127.0.0.1:1/"]


def test_open_browser_failure_is_not_fatal(monkeypatch):
    monkeypatch.delenv("ATLAS_NO_BROWSER", raising=False)

    def fail(_url):
        raise webbrowser.Error("brak przeglądarki")

    monkeypatch.setattr(webbrowser, "open", fail)
    runner.open_browser("http://127.0.0.1:1/")  # nie rzuca wyjątku


def test_serve_forever_runs_until_ctrl_c(workspace, monkeypatch, capsys):
    def interrupt(_seconds):
        raise KeyboardInterrupt

    runner.ensure_ui(workspace)  # serwer startuje przed podmianą sleep (czeka na start w pętli z sleep)
    monkeypatch.setattr(runner.time, "sleep", interrupt)
    runner.serve_forever(workspace, open_in_browser=False)
    assert "SSDT Atlas UI: http://127.0.0.1:" in capsys.readouterr().out


def test_server_start_timeout(workspace, monkeypatch):
    class NeverStarts:
        started = False

        def __init__(self, config):
            pass

        def run(self):
            pass

    class FakeTime:
        """Zegar widziany tylko przez runner: pierwszy odczyt 0 s, każdy następny 100 s (po terminie)."""

        now = 0

        @classmethod
        def monotonic(cls):
            value, cls.now = cls.now, 100
            return value

        @staticmethod
        def sleep(_seconds):
            pass

    monkeypatch.setattr(runner.uvicorn, "Server", NeverStarts)
    monkeypatch.setattr(runner, "time", FakeTime)
    with pytest.raises(RuntimeError, match="nie uruchomiła się w 15 s"):
        runner.ensure_ui(workspace)


def test_serve_forever_opens_browser(workspace, monkeypatch):
    opened = []
    runner.ensure_ui(workspace)
    monkeypatch.setattr(runner, "open_browser", opened.append)
    monkeypatch.setattr(runner.time, "sleep", lambda _s: (_ for _ in ()).throw(KeyboardInterrupt))
    runner.serve_forever(workspace)
    assert opened == [workspace.ui_url]


def test_close_ui_stops_server_and_removes_lock(workspace):
    url = runner.ensure_ui(workspace)
    assert runner.close_ui(workspace) == {"closed": True, "url": url}

    assert workspace.ui_url is None
    assert not (workspace.data_dir / "ui.lock").exists()
    with pytest.raises(httpx.HTTPError):
        httpx.get(url + "api/ping", timeout=2)
    assert runner.ensure_ui(workspace) != "", "aplikację można uruchomić ponownie"


def test_close_ui_when_not_running(workspace):
    assert runner.close_ui(workspace)["closed"] is False


def test_close_ui_started_by_other_session(workspace):
    url = runner.ensure_ui(workspace)
    other = Workspace(workspace.project_dir)
    try:
        result = runner.close_ui(other)
        assert (result["closed"], result["url"]) == (False, url)
        assert "inna sesja" in result["reason"]
        assert ping(url)["ok"] is True, "aplikacja innej sesji działa dalej"
    finally:
        other.close()


def test_close_ui_keeps_lock_of_other_server(workspace):
    runner.ensure_ui(workspace)
    lock = workspace.data_dir / "ui.lock"
    lock.write_text(json.dumps({"url": "http://127.0.0.1:1/"}), encoding="utf-8")
    assert runner.close_ui(workspace)["closed"] is True
    assert lock.exists(), "ui.lock wskazuje inny serwer: zostaje"

    lock.write_text("zepsuty", encoding="utf-8")
    runner.ensure_ui(workspace)
    lock.write_text("zepsuty", encoding="utf-8")
    assert runner.close_ui(workspace)["closed"] is True
