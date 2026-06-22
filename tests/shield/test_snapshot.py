from types import SimpleNamespace

from almc_shield.shield.snapshot import Snapshot, gather
from almc_shield.state import State


class FakeF2b:
    def status_count(self):
        return 28126


def _cfg():
    return SimpleNamespace(
        api=SimpleNamespace(url="https://almc.es/api/v1/abuse"),
        puller=SimpleNamespace(include_global=True),
        fail2ban=SimpleNamespace(jail_name="almc-blocklist"),
        heartbeat=SimpleNamespace(interval_seconds=60),
    )


class FakeOutbox:
    def size(self):
        return 3

    def oldest_age_seconds(self):
        return 4


def test_gather_builds_snapshot(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.add_applied("1.1.1.1", "tenant")
    state.add_applied("2.2.2.2", "global")
    state.set_global_cursor(27984)
    state.set_cursor(5000)
    state.set_feed_stats(142, 28000)
    state.set_snapshot({
        "snapshot_at": 1990.0,
        "started_at": 1000.0, "hb_last_at": 1990.0, "hb_ok": True,
        "status": "healthy",
        "threads": {"reader": True, "sender": True, "puller": True, "heartbeat": True},
        "last_pull_at": 1980.0, "last_flush_at": 1995.0,
    })
    snap = gather(_cfg(), state, FakeOutbox(), FakeF2b(), now=2000.0)
    assert isinstance(snap, Snapshot)
    assert snap.status == "healthy"
    assert snap.uptime_seconds == 1000
    assert snap.hb_age_seconds == 10
    assert snap.applied_total == 2
    assert snap.applied_tenant == 1
    assert snap.applied_global == 1
    assert snap.global_cursor == 27984
    assert snap.feed_global_active == 28000
    assert snap.jail_banned == 28126
    assert snap.outbox_pending == 3
    assert snap.threads["puller"] is True
    assert snap.backend_url == "https://almc.es/api/v1/abuse"


def test_gather_no_snapshot_marks_critical(tmp_path):
    state = State(str(tmp_path / "state.db"))
    snap = gather(_cfg(), state, FakeOutbox(), FakeF2b(), now=2000.0)
    assert snap.status == "critical"   # agente no ha escrito snapshot -> caido
    assert snap.snapshot_age_seconds is None
