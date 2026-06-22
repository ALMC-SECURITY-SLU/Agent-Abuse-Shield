from rich.console import Console

from almc_shield.shield.render import render_snapshot
from almc_shield.shield.snapshot import Snapshot


def _snap(**over):
    base = dict(
        version="1.0.6", hostname="andromeda.compsa.net", status="healthy",
        started_at=1.0, uptime_seconds=100000, hb_last_at=1.0, hb_age_seconds=23,
        hb_ok=True, threads={"reader": True, "sender": True, "puller": True, "heartbeat": True},
        backend_url="https://almc.es/api/v1/abuse", feed_global_enabled=True,
        applied_total=28126, applied_tenant=142, applied_global=27984,
        tenant_cursor=5000, global_cursor=27984, feed_global_active=28000,
        jail_name="almc-blocklist", jail_banned=28126, outbox_pending=3,
        outbox_oldest_seconds=4, last_pull_at=1.0, last_pull_age_seconds=12, last_flush_at=1.0,
        snapshot_age_seconds=23,
        recent=[("185.220.101.34", 1718000000, "global"), ("45.134.26.7", 1718000010, "tenant")],
    )
    base.update(over)
    return Snapshot(**base)


def _text(snap, rows=10, source="all"):
    con = Console(record=True, width=100, color_system=None)
    con.print(render_snapshot(snap, rows=rows, source=source))
    return con.export_text()


def test_render_contains_key_values():
    out = _text(_snap())
    assert "HEALTHY" in out
    assert "andromeda.compsa.net" in out
    assert "1.0.6" in out
    assert "28126" in out or "28.126" in out
    assert "almc-blocklist" in out
    assert "185.220.101.34" in out


def test_render_critical_status_shown():
    out = _text(_snap(status="critical"))
    assert "CRITICAL" in out


def test_render_source_filter():
    out = _text(_snap(), source="tenant")
    assert "45.134.26.7" in out          # tenant
    assert "185.220.101.34" not in out   # global filtrado
