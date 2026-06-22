import json

import pytest

from almc_shield.shield import cli


class _FakeF2b:
    def status_count(self):
        return 0


@pytest.fixture
def cfg_file(tmp_path):
    p = tmp_path / "c.ini"
    db = tmp_path / "outbox.db"
    p.write_text(
        "[api]\nurl = https://almc.es/api/v1/abuse\napi_key = ab_live_x\n"
        f"[outbox]\ndb_path = {db}\n"
    )
    return str(p)


def test_check_returns_exit_code(cfg_file, monkeypatch):
    # sin snapshot -> critical -> exit 2
    monkeypatch.setattr(cli, "_build_f2b", lambda cfg: _FakeF2b())
    rc = cli.main(["status", "--check", "-c", cfg_file])
    assert rc == 2


def test_json_outputs_parseable(cfg_file, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_build_f2b", lambda cfg: _FakeF2b())
    rc = cli.main(["status", "--json", "-c", cfg_file])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["version"]
    assert "status" in data
    assert rc == 0


def test_once_prints_human(cfg_file, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_build_f2b", lambda cfg: _FakeF2b())
    rc = cli.main(["status", "--once", "--no-color", "-c", cfg_file])
    out = capsys.readouterr().out
    assert "ALMC Shield" in out
    assert rc == 0


def test_cli_dispatches_disable(cfg_file, monkeypatch):
    calls = {}

    def fake_disable(cfg, assume_yes=False):
        calls["yes"] = assume_yes
        return 0

    monkeypatch.setattr(cli.actions, "disable", fake_disable)
    rc = cli.main(["disable", "--yes", "-c", cfg_file])
    assert rc == 0
    assert calls["yes"] is True


def test_cli_dispatches_update(cfg_file, monkeypatch):
    calls = {}

    def fake_update(cfg, config_path, assume_yes=False):
        calls["cp"] = config_path
        return 0

    monkeypatch.setattr(cli.actions, "update", fake_update)
    rc = cli.main(["update", "--yes", "-c", cfg_file])
    assert rc == 0
    assert calls["cp"] == cfg_file


def test_cli_dispatches_feed_global_off(cfg_file, monkeypatch):
    calls = {}

    def fake_feed(cfg, config_path, turn_on=None, state=None, f2b=None, assume_yes=False):
        calls["turn_on"] = turn_on
        return 0

    monkeypatch.setattr(cli.actions, "feed_global", fake_feed)
    rc = cli.main(["feed-global", "off", "-c", cfg_file])
    assert rc == 0
    assert calls["turn_on"] is False


def test_cli_feed_global_requires_on_off(cfg_file):
    rc = cli.main(["feed-global", "-c", cfg_file])
    assert rc == 2
