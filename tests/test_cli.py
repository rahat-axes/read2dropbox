"""Offline CLI tests via Typer's CliRunner.

No network and no real config dir: TokenStore, the sync engine, and the Read client
are all monkeypatched with fakes, so argument parsing, --full/--days wiring, output,
and exit codes are exercised deterministically.
"""

from __future__ import annotations

import time

import pytest
from typer.testing import CliRunner

from readbox import cli
from readbox.auth import AuthError
from readbox.read_client import ReadAPIError
from readbox.sync import SyncResult

runner = CliRunner()


class FakeStore:
    def __init__(self, data=None):
        self._data = data or {}
        self.path = "/fake/tokens.json"

    def get(self, provider):
        return dict(self._data.get(provider, {}))


def _output(result) -> str:
    """Combined stdout+stderr, tolerant of Typer's stream handling across versions."""
    out = result.output or ""
    try:
        out += result.stderr or ""
    except (ValueError, AttributeError):
        pass
    return out


# -- sync --------------------------------------------------------------------


def test_sync_default_is_incremental(monkeypatch):
    captured = {}

    def fake_run_sync(*, full, start_time_ms, logger):
        captured.update(full=full, start_time_ms=start_time_ms)
        logger("+ synced 01A")
        return SyncResult(uploaded=["01A"], skipped=["01B"])

    monkeypatch.setattr(cli.sync_mod, "run_sync", fake_run_sync)
    result = runner.invoke(cli.app, ["sync"])

    assert result.exit_code == 0
    assert captured == {"full": False, "start_time_ms": None}
    assert "1 uploaded, 1 unchanged, 0 failed" in result.output


def test_sync_full_flag(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        cli.sync_mod,
        "run_sync",
        lambda **kw: captured.update(kw) or SyncResult(uploaded=["x"]),
    )
    result = runner.invoke(cli.app, ["sync", "--full"])
    assert result.exit_code == 0
    assert captured["full"] is True


def test_sync_days_sets_start_time_ms(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        cli.sync_mod,
        "run_sync",
        lambda **kw: captured.update(kw) or SyncResult(),
    )
    result = runner.invoke(cli.app, ["sync", "--days", "7"])
    assert result.exit_code == 0
    seven_days_ago = int((time.time() - 7 * 86400) * 1000)
    # Within a few seconds of the expected cutoff.
    assert abs(captured["start_time_ms"] - seven_days_ago) < 5000


def test_sync_auth_error_exits_1(monkeypatch):
    def boom(**kw):
        raise AuthError("not authenticated with Read")

    monkeypatch.setattr(cli.sync_mod, "run_sync", boom)
    result = runner.invoke(cli.app, ["sync"])
    assert result.exit_code == 1
    assert "readbox auth" in _output(result)


def test_sync_partial_failure_exits_1(monkeypatch):
    monkeypatch.setattr(
        cli.sync_mod,
        "run_sync",
        lambda **kw: SyncResult(uploaded=["ok"], failed=[("bad", "boom")]),
    )
    result = runner.invoke(cli.app, ["sync"])
    assert result.exit_code == 1
    assert "bad" in _output(result)


# -- list --------------------------------------------------------------------


class FakeReadClient:
    def __init__(self, token):
        self.token = token

    def list_meetings(self, *, start_time_ms=None):
        return [
            {"id": "01A", "start_time_ms": 1787328000000, "title": "Engineering Sync"},
            {"id": "01B", "start_time_ms": 1787257800000, "title": "Q4 Roadmap"},
        ]


def test_list_prints_meetings(monkeypatch):
    monkeypatch.setattr(cli, "TokenStore", lambda: FakeStore())
    monkeypatch.setattr(cli, "read_access_token", lambda store: "tok")
    monkeypatch.setattr(cli, "ReadClient", FakeReadClient)

    result = runner.invoke(cli.app, ["list"])
    assert result.exit_code == 0
    assert "Engineering Sync" in result.output
    assert "01A" in result.output
    assert "2026-08-21" in result.output
    assert "2 meeting(s)" in result.output


def test_list_empty(monkeypatch):
    class Empty(FakeReadClient):
        def list_meetings(self, *, start_time_ms=None):
            return []

    monkeypatch.setattr(cli, "TokenStore", lambda: FakeStore())
    monkeypatch.setattr(cli, "read_access_token", lambda store: "tok")
    monkeypatch.setattr(cli, "ReadClient", Empty)

    result = runner.invoke(cli.app, ["list"])
    assert result.exit_code == 0
    assert "No meetings found" in result.output


def test_list_read_error_exits_1(monkeypatch):
    monkeypatch.setattr(cli, "TokenStore", lambda: FakeStore())

    def boom(store):
        raise ReadAPIError(500, "server on fire")

    monkeypatch.setattr(cli, "read_access_token", boom)
    result = runner.invoke(cli.app, ["list"])
    assert result.exit_code == 1


# -- status ------------------------------------------------------------------


def test_status_reports_auth_and_sync_state(monkeypatch, tmp_path):
    store = FakeStore(
        {"read": {"refresh_token": "r"}, "dropbox": {"refresh_token": "d"}}
    )
    monkeypatch.setattr(cli, "TokenStore", lambda: store)
    monkeypatch.setattr(cli.config, "state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(cli.config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        cli.sync_mod,
        "load_state",
        lambda path: {"version": 1, "meetings": {"01A": {"synced_at": 1790000000.0}}},
    )

    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    out = result.output
    assert "Read AI" in out and "Dropbox" in out
    assert "Synced meetings tracked: 1" in out


def test_status_flags_missing_auth(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "TokenStore", lambda: FakeStore())
    monkeypatch.setattr(cli.config, "state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(cli.config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cli.sync_mod, "load_state", lambda path: {"meetings": {}})

    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "Synced meetings tracked: 0" in result.output


# -- config ------------------------------------------------------------------


def test_config_show_lists_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.config, "settings_path", lambda: tmp_path / "settings.json")
    result = runner.invoke(cli.app, ["config"])
    assert result.exit_code == 0
    assert "root_folder = Read AI" in result.output
    assert "Example path:" in result.output


def test_config_set_persists_value(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(cli.config, "settings_path", lambda: path)
    result = runner.invoke(cli.app, ["config", "root_folder", "Meetings"])
    assert result.exit_code == 0
    assert "root_folder = Meetings" in result.output
    assert cli.config.load_settings(path).root_folder == "Meetings"


def test_config_rejects_unknown_key(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.config, "settings_path", lambda: tmp_path / "settings.json")
    result = runner.invoke(cli.app, ["config", "bogus", "x"])
    assert result.exit_code == 1


def test_config_rejects_invalid_template(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(cli.config, "settings_path", lambda: path)
    # Unbalanced brace → malformed template → rejected, nothing written.
    result = runner.invoke(cli.app, ["config", "filename_template", "{title"])
    assert result.exit_code == 1
    assert not path.exists()


# -- help --------------------------------------------------------------------


def test_help_lists_all_commands():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("auth", "sync", "list", "status"):
        assert cmd in result.output
