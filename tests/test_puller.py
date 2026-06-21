"""Tests for puller.py — delta drain-loop + full-sync pagination."""
from types import SimpleNamespace

import pytest

from almc_shield.puller import MAX_FULL_PAGES, MAX_PULL_PAGES_PER_CYCLE, Puller
from almc_shield.state import State


# ---- fakes -----------------------------------------------------------------

class FakeF2b:
    def __init__(self):
        self.banned = []
        self.unbanned = []

    def banip(self, ip):
        self.banned.append(ip)
        return True

    def unbanip(self, ip):
        self.unbanned.append(ip)
        return True

    def server_pid(self):
        return 1234


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class FakeHttpClient:
    """Returns queued responses in order; falls back to an empty,
    non-advancing response that terminates any drain loop."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, path, params=None, timeout=None):
        params = params or {}
        self.calls.append({"path": path, "params": params})
        if self._responses:
            return self._responses.pop(0)
        return FakeResponse(200, {
            "cursor": params.get("since", 0),
            "next_cursor": None,
            "global_cursor": params.get("global_since", 0),
            "items": [],
        })


class AlwaysMoreClient:
    """Always claims there is another tenant page (cursor advances)."""

    def __init__(self):
        self.calls = []

    def get(self, path, params=None, timeout=None):
        params = params or {}
        self.calls.append({"path": path, "params": params})
        since = params.get("since", 0)
        return FakeResponse(200, {
            "cursor": since + 1,
            "next_cursor": since + 1,
            "global_cursor": params.get("global_since", 0),
            "items": [],
        })


def make_puller(state, client, f2b=None):
    cfg = SimpleNamespace(puller=SimpleNamespace(interval_seconds=300))
    return Puller(cfg, state, client, f2b or FakeF2b())


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("almc_shield.puller.time.sleep", lambda *a, **k: None)


def _global_adds(ips):
    return [{"ip": ip, "op": "add", "source": "global"} for ip in ips]


def _tenant_adds(ips):
    return [{"ip": ip, "op": "add", "source": "tenant"} for ip in ips]


# ---- pull_once: global_since + drain loop -----------------------------------

def test_pull_once_sends_global_since(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.set_global_cursor(7)
    client = FakeHttpClient([
        FakeResponse(200, {"cursor": 0, "next_cursor": None,
                           "global_cursor": 7, "items": []}),
    ])
    make_puller(state, client).pull_once()
    assert client.calls[0]["params"]["global_since"] == 7


def test_pull_once_advances_global_cursor(tmp_path):
    state = State(str(tmp_path / "state.db"))
    client = FakeHttpClient([
        FakeResponse(200, {"cursor": 0, "next_cursor": None,
                           "global_cursor": 42, "items": []}),
    ])
    make_puller(state, client).pull_once()
    assert state.get_global_cursor() == 42


def test_pull_once_drains_global_in_one_cycle(tmp_path):
    state = State(str(tmp_path / "state.db"))
    f2b = FakeF2b()
    client = FakeHttpClient([
        FakeResponse(200, {"cursor": 0, "next_cursor": None, "global_cursor": 10,
                           "items": _global_adds(["1.0.0.1", "1.0.0.2"])}),
        FakeResponse(200, {"cursor": 0, "next_cursor": None, "global_cursor": 20,
                           "items": _global_adds(["1.0.0.3", "1.0.0.4"])}),
        FakeResponse(200, {"cursor": 0, "next_cursor": None, "global_cursor": 25,
                           "items": _global_adds(["1.0.0.5"])}),
    ])
    make_puller(state, client, f2b).pull_once()
    assert f2b.banned == ["1.0.0.1", "1.0.0.2", "1.0.0.3", "1.0.0.4", "1.0.0.5"]
    assert state.get_global_cursor() == 25
    # 3 batches + 1 terminating (no-advance) call
    assert len(client.calls) == 4
    assert [c["params"]["global_since"] for c in client.calls] == [0, 10, 20, 25]


def test_pull_once_follows_tenant_next_cursor(tmp_path):
    state = State(str(tmp_path / "state.db"))
    f2b = FakeF2b()
    client = FakeHttpClient([
        FakeResponse(200, {"cursor": 100, "next_cursor": 100, "global_cursor": 0,
                           "items": _tenant_adds(["2.0.0.1"])}),
        FakeResponse(200, {"cursor": 200, "next_cursor": None, "global_cursor": 0,
                           "items": _tenant_adds(["2.0.0.2"])}),
    ])
    make_puller(state, client, f2b).pull_once()
    assert f2b.banned == ["2.0.0.1", "2.0.0.2"]
    assert state.get_cursor() == 200
    assert len(client.calls) == 2


def test_pull_once_cursors_are_independent(tmp_path):
    state = State(str(tmp_path / "state.db"))
    client = FakeHttpClient([
        FakeResponse(200, {"cursor": 0, "next_cursor": None, "global_cursor": 50,
                           "items": _global_adds(["3.0.0.1"])}),
    ])
    make_puller(state, client).pull_once()
    assert state.get_cursor() == 0
    assert state.get_global_cursor() == 50


def test_pull_once_handles_remove_op(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.add_applied("4.0.0.1", "tenant")
    f2b = FakeF2b()
    client = FakeHttpClient([
        FakeResponse(200, {"cursor": 5, "next_cursor": None, "global_cursor": 0,
                           "items": [{"ip": "4.0.0.1", "op": "remove"}]}),
    ])
    make_puller(state, client, f2b).pull_once()
    assert f2b.unbanned == ["4.0.0.1"]
    assert state.count_applied() == 0


def test_pull_once_include_global_false_sends_false_and_terminates(tmp_path):
    state = State(str(tmp_path / "state.db"))
    client = FakeHttpClient([
        FakeResponse(200, {"cursor": 0, "next_cursor": None, "global_cursor": 0,
                           "items": []}),
    ])
    p = make_puller(state, client)
    p.include_global = False
    p.pull_once()
    assert client.calls[0]["params"]["include_global"] == "false"
    assert len(client.calls) == 1


def test_pull_once_respects_safety_cap(tmp_path):
    state = State(str(tmp_path / "state.db"))
    client = AlwaysMoreClient()
    make_puller(state, client).pull_once()
    assert len(client.calls) == MAX_PULL_PAGES_PER_CYCLE


def test_pull_once_http_error_does_not_advance(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.set_cursor(11)
    state.set_global_cursor(22)
    client = FakeHttpClient([FakeResponse(500, {}, text="boom")])
    make_puller(state, client).pull_once()  # must not raise
    assert state.get_cursor() == 11
    assert state.get_global_cursor() == 22


class AlwaysMoreFullClient:
    """Always returns a next_page (for the /blocklist/full cap test)."""

    def __init__(self):
        self.calls = []

    def get(self, path, params=None, timeout=None):
        params = params or {}
        self.calls.append({"path": path, "params": params})
        page = params.get("page", 1)
        return FakeResponse(200, {"page": page, "next_page": page + 1, "items": []})


# ---- _full_sync: page iteration --------------------------------------------

def test_full_sync_iterates_all_pages(tmp_path):
    state = State(str(tmp_path / "state.db"))
    f2b = FakeF2b()
    client = FakeHttpClient([
        FakeResponse(200, {"page": 1, "next_page": 2,
                           "items": [{"ip": "5.0.0.1", "source": "tenant"},
                                     {"ip": "5.0.0.2", "source": "tenant"}]}),
        FakeResponse(200, {"page": 2, "next_page": 3,
                           "items": [{"ip": "5.0.0.3", "source": "tenant"},
                                     {"ip": "5.0.0.4", "source": "tenant"}]}),
        FakeResponse(200, {"page": 3, "next_page": None,
                           "items": [{"ip": "5.0.0.5", "source": "global"}]}),
    ])
    make_puller(state, client, f2b)._full_sync()
    assert len(f2b.banned) == 5
    assert state.count_applied() == 5
    assert [c["params"]["page"] for c in client.calls] == [1, 2, 3]


def test_full_sync_single_page(tmp_path):
    state = State(str(tmp_path / "state.db"))
    f2b = FakeF2b()
    client = FakeHttpClient([
        FakeResponse(200, {"page": 1, "next_page": None,
                           "items": [{"ip": "6.0.0.1", "source": "tenant"}]}),
    ])
    make_puller(state, client, f2b)._full_sync()
    assert len(client.calls) == 1
    assert f2b.banned == ["6.0.0.1"]
    assert state.count_applied() == 1


def test_full_sync_respects_safety_cap(tmp_path):
    state = State(str(tmp_path / "state.db"))
    client = AlwaysMoreFullClient()
    make_puller(state, client)._full_sync()
    assert len(client.calls) == MAX_FULL_PAGES


def test_full_sync_http_error_mid_pagination_stops(tmp_path):
    state = State(str(tmp_path / "state.db"))
    f2b = FakeF2b()
    client = FakeHttpClient([
        FakeResponse(200, {"page": 1, "next_page": 2,
                           "items": [{"ip": "7.0.0.1", "source": "tenant"}]}),
        FakeResponse(500, {}, text="boom"),
    ])
    make_puller(state, client, f2b)._full_sync()  # must not raise
    assert f2b.banned == ["7.0.0.1"]   # page 1 applied before the error
    assert len(client.calls) == 2      # tried page 2, then stopped
