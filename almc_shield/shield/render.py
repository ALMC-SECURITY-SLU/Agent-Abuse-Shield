"""Funciones puras: Snapshot -> renderables rich."""
from __future__ import annotations

import time

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from almc_shield.shield.snapshot import Snapshot

_STATUS_STYLE = {"healthy": "bold green", "degraded": "bold yellow",
                 "critical": "bold red", "unknown": "bold red"}


def _age(sec) -> str:
    if sec is None:
        return "—"
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    return f"{sec // 3600}h {(sec % 3600) // 60}m"


def _dot(ok: bool) -> Text:
    return Text("●", style="green" if ok else "red")


def header_panel(s: Snapshot) -> Panel:
    style = _STATUS_STYLE.get(s.status, "bold red")
    feed = "ON" if s.feed_global_enabled else "OFF"
    body = Text()
    body.append("● ", style=style)
    body.append(s.status.upper(), style=style)
    body.append(f"   backend {s.backend_url}")
    body.append(f"   feed global {feed}   v{s.version}   up {_age(s.uptime_seconds)}")
    return Panel(body, title=f"ALMC Shield · {s.hostname}", border_style=style)


def feed_panel(s: Snapshot) -> Panel:
    body = Text()
    if s.feed_global_active:
        pct = min(100, int(100 * s.applied_global / s.feed_global_active))
        filled = pct * 20 // 100
        bar = "▓" * filled + "░" * (20 - filled)
        body.append(f"Feed global  [{bar}]  {s.applied_global} / {s.feed_global_active}  ({pct}%)\n",
                    style="cyan")
    else:
        body.append(f"Feed global  {s.applied_global} aplicadas (total desconocido)\n", style="cyan")
    body.append(f"Aplicadas {s.applied_total}  ·  tenant {s.applied_tenant} · global {s.applied_global}"
                f"  ·  jail {s.jail_name} ")
    body.append("● ", style="green")
    banned = "—" if s.jail_banned is None else str(s.jail_banned)
    body.append(f"({banned} baneadas)   ↻ pull hace {_age(s.last_pull_age_seconds)}")
    return Panel(body, border_style="cyan")


def queue_panel(s: Snapshot) -> Panel:
    body = Text()
    body.append(f"Cola envío  {s.outbox_pending} pendientes · más viejo {_age(s.outbox_oldest_seconds)}\n")
    body.append("Heartbeat ")
    body.append(f"{'✓ OK' if s.hb_ok else '✗ fallo'} hace {_age(s.hb_age_seconds)}",
                style="green" if s.hb_ok else "red")
    body.append("     Hilos: ")
    for name in ("reader", "sender", "puller", "heartbeat"):
        body.append(f"{name}")
        body.append_text(_dot(bool(s.threads.get(name))))
        body.append(" ")
    return Panel(body, border_style="blue")


def bans_table(s: Snapshot, rows: int, source: str) -> Panel:
    table = Table(expand=True, show_edge=False)
    table.add_column("hora")
    table.add_column("IP")
    table.add_column("fuente")
    shown = 0
    for ip, applied_at, src in s.recent:
        if source != "all" and src != source:
            continue
        if shown >= rows:
            break
        hora = time.strftime("%H:%M:%S", time.localtime(applied_at)) if applied_at else "—"
        style = "yellow" if src == "global" else "cyan"
        table.add_row(hora, ip, Text(src, style=style))
        shown += 1
    return Panel(table, title="Últimos bloqueos", border_style="white")


def render_snapshot(s: Snapshot, rows: int = 10, source: str = "all"):
    return Group(header_panel(s), feed_panel(s), queue_panel(s), bans_table(s, rows, source))
