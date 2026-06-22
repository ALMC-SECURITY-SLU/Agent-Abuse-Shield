from types import SimpleNamespace

import pytest

from almc_shield.shield import actions
from almc_shield.state import State


class FakeRun:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, *a, **k):
        self.calls.append(cmd)
        return SimpleNamespace(returncode=0)


class FakeReport:
    def __init__(self):
        self.events = []

    def __call__(self, cfg, event, hostname=None):
        self.events.append(event)


class FakeF2b:
    def __init__(self):
        self.unbanned = []

    def unbanip(self, ip):
        self.unbanned.append(ip)
        return True


def _cfg():
    return SimpleNamespace(api=SimpleNamespace(url="https://almc.es/api/v1/abuse", api_key="ab_live_x"))


def _cfg_file(tmp_path, include_global=True):
    p = tmp_path / "c.ini"
    p.write_text(
        "[api]\nurl = https://almc.es/api/v1/abuse\napi_key = ab_live_x\n"
        f"[puller]\ninclude_global = {'true' if include_global else 'false'}\n"
    )
    return str(p)


@pytest.fixture
def root(monkeypatch):
    monkeypatch.setattr(actions, "is_root", lambda: True)


# ---- helpers ----

def test_confirm_assume_yes():
    assert actions.confirm("x", True) is True


def test_confirm_typed():
    assert actions.confirm("x", False, _input=lambda p: "si") is True
    assert actions.confirm("x", False, _input=lambda p: "no") is False


def test_confirm_eof_false():
    def boom(p):
        raise EOFError
    assert actions.confirm("x", False, _input=boom) is False


def test_report_event_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("net down")
    monkeypatch.setattr(actions.httpx, "post", boom)
    actions.report_event(_cfg(), "disabled")  # must not raise


# ---- guardas ----

def test_actions_require_root(monkeypatch):
    monkeypatch.setattr(actions, "is_root", lambda: False)
    assert actions.disable(_cfg(), assume_yes=True, run=FakeRun(), report=FakeReport()) == 2
    assert actions.enable(_cfg(), run=FakeRun(), report=FakeReport()) == 2
    assert actions.uninstall(_cfg(), assume_yes=True, run=FakeRun(), report=FakeReport()) == 2


# ---- disable / enable ----

def test_disable(root):
    run, rep = FakeRun(), FakeReport()
    assert actions.disable(_cfg(), assume_yes=True, run=run, report=rep) == 0
    assert ["systemctl", "disable", "--now", "almc-shield"] in run.calls
    assert "disabled" in rep.events


def test_disable_cancel(root, monkeypatch):
    monkeypatch.setattr(actions, "confirm", lambda *a, **k: False)
    run, rep = FakeRun(), FakeReport()
    assert actions.disable(_cfg(), assume_yes=False, run=run, report=rep) == 1
    assert run.calls == []  # no ejecuta nada
    assert rep.events == []


def test_enable(root):
    run, rep = FakeRun(), FakeReport()
    assert actions.enable(_cfg(), run=run, report=rep) == 0
    assert ["systemctl", "enable", "--now", "almc-shield"] in run.calls
    assert "enabled" in rep.events


# ---- update ----

def test_update(root, tmp_path):
    run, rep = FakeRun(), FakeReport()
    rc = actions.update(_cfg(), _cfg_file(tmp_path), assume_yes=True, run=run, report=rep)
    assert rc == 0
    assert any("install.sh" in part for cmd in run.calls for part in cmd)
    assert "updated" in rep.events


# ---- uninstall ----

def test_uninstall_reports_before_running(root, monkeypatch):
    monkeypatch.setattr(actions, "confirm", lambda *a, **k: True)
    order = []
    rep = lambda cfg, event, hostname=None: order.append(("report", event))
    run = lambda cmd, *a, **k: order.append(("run", cmd)) or SimpleNamespace(returncode=0)
    assert actions.uninstall(_cfg(), assume_yes=True, run=run, report=rep) == 0
    assert order[0] == ("report", "uninstalled")   # reporta ANTES de borrarse
    assert order[1][0] == "run"


# ---- feed-global ----

def test_feed_global_off_removes_global_ips(root, monkeypatch, tmp_path):
    monkeypatch.setattr(actions, "confirm", lambda *a, **k: True)
    state = State(str(tmp_path / "state.db"))
    state.add_applied("9.9.9.1", "global")
    state.add_applied("9.9.9.2", "global")
    state.add_applied("1.1.1.1", "tenant")
    f2b, run, rep = FakeF2b(), FakeRun(), FakeReport()
    cfg_path = _cfg_file(tmp_path, include_global=True)
    rc = actions.feed_global(_cfg(), cfg_path, turn_on=False, state=state, f2b=f2b,
                             assume_yes=True, run=run, report=rep)
    assert rc == 0
    assert sorted(f2b.unbanned) == ["9.9.9.1", "9.9.9.2"]
    assert state.count_by_source("global") == 0
    assert state.count_by_source("tenant") == 1
    assert ["systemctl", "restart", "almc-shield"] in run.calls
    assert "feed_global_off" in rep.events
    # config quedó en include_global = false
    assert "include_global = false" in open(cfg_path, encoding="utf-8").read()


def test_feed_global_on(root, tmp_path):
    state = State(str(tmp_path / "state.db"))
    f2b, run, rep = FakeF2b(), FakeRun(), FakeReport()
    cfg_path = _cfg_file(tmp_path, include_global=False)
    rc = actions.feed_global(_cfg(), cfg_path, turn_on=True, state=state, f2b=f2b,
                             run=run, report=rep)
    assert rc == 0
    assert ["systemctl", "restart", "almc-shield"] in run.calls
    assert "feed_global_on" in rep.events
    assert "include_global = true" in open(cfg_path, encoding="utf-8").read()
