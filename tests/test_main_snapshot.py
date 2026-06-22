from types import SimpleNamespace

from almc_shield.main import build_status_snapshot


def test_build_status_snapshot_shape():
    agent = SimpleNamespace(
        heartbeat=SimpleNamespace(degraded=False, _start_time=1000.0, last_ok_at=1990.0,
                                  is_alive=lambda: True),
        puller=SimpleNamespace(last_pull_at=1980.0, is_alive=lambda: True),
        reader=SimpleNamespace(is_alive=lambda: True),
        sender=SimpleNamespace(),
        _last_flush_at=1995.0,
    )
    snap = build_status_snapshot(agent)
    assert snap["status"] == "healthy"
    assert snap["started_at"] == 1000.0
    assert snap["hb_last_at"] == 1990.0
    assert snap["threads"] == {"reader": True, "sender": True, "puller": True, "heartbeat": True}
    assert snap["last_pull_at"] == 1980.0
    assert snap["last_flush_at"] == 1995.0


def test_build_status_snapshot_degraded_and_dead_thread():
    agent = SimpleNamespace(
        heartbeat=SimpleNamespace(degraded=True, _start_time=1.0, last_ok_at=None,
                                  is_alive=lambda: True),
        puller=SimpleNamespace(last_pull_at=None, is_alive=lambda: False),
        reader=SimpleNamespace(is_alive=lambda: True),
        sender=SimpleNamespace(),
        _last_flush_at=None,
    )
    snap = build_status_snapshot(agent)
    assert snap["status"] == "critical"   # un hilo muerto => critical
