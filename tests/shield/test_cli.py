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
