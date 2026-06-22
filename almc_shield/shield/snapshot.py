"""Recolecta el estado del agente desde fuentes locales en un Snapshot plano."""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field

from almc_shield.shield.health import effective_status
from almc_shield.version import __version__


@dataclass
class Snapshot:
    version: str
    hostname: str
    status: str
    started_at: float | None
    uptime_seconds: int | None
    hb_last_at: float | None
    hb_age_seconds: int | None
    hb_ok: bool
    threads: dict
    backend_url: str
    feed_global_enabled: bool
    applied_total: int
    applied_tenant: int
    applied_global: int
    tenant_cursor: int
    global_cursor: int
    feed_global_active: int | None
    jail_name: str
    jail_banned: int | None
    outbox_pending: int
    outbox_oldest_seconds: int | None
    last_pull_at: float | None
    last_pull_age_seconds: int | None
    last_flush_at: float | None
    snapshot_age_seconds: int | None
    recent: list = field(default_factory=list)


def _age(now: float, ts) -> int | None:
    if ts is None:
        return None
    return max(0, int(now - float(ts)))


def gather(cfg, state, outbox, f2b, now: float | None = None) -> Snapshot:
    now = time.time() if now is None else now
    snap = state.get_snapshot() or {}
    feed = state.get_feed_stats() or {}
    # Frescura = cuándo el agente escribió el snapshot (snapshot_at), NO el heartbeat:
    # un agente vivo con heartbeat degradado sigue escribiendo snapshot.
    snap_age = _age(now, snap.get("snapshot_at")) if snap else None
    max_age = getattr(cfg.heartbeat, "interval_seconds", 60) * 2
    status = effective_status(snap.get("status"), snap_age, max_age)
    return Snapshot(
        version=__version__,
        hostname=socket.gethostname(),
        status=status,
        started_at=snap.get("started_at"),
        uptime_seconds=_age(now, snap.get("started_at")),
        hb_last_at=snap.get("hb_last_at"),
        hb_age_seconds=_age(now, snap.get("hb_last_at")),
        hb_ok=bool(snap.get("hb_ok", False)),
        threads=snap.get("threads", {}),
        backend_url=cfg.api.url,
        feed_global_enabled=bool(cfg.puller.include_global),
        applied_total=state.count_applied(),
        applied_tenant=state.count_by_source("tenant"),
        applied_global=state.count_by_source("global"),
        tenant_cursor=state.get_cursor(),
        global_cursor=state.get_global_cursor(),
        feed_global_active=feed.get("global_active"),
        jail_name=cfg.fail2ban.jail_name,
        jail_banned=f2b.status_count(),
        outbox_pending=outbox.size(),
        outbox_oldest_seconds=outbox.oldest_age_seconds(),
        last_pull_at=snap.get("last_pull_at"),
        last_pull_age_seconds=_age(now, snap.get("last_pull_at")),
        last_flush_at=snap.get("last_flush_at"),
        snapshot_age_seconds=snap_age,
        recent=state.recent_applied(limit=50),
    )
