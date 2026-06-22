"""CLI `shield`: visor del agente (status/--once/--json/--check)."""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from almc_shield.config import load as load_config
from almc_shield.f2b_client import F2bClient
from almc_shield.outbox import Outbox
from almc_shield.shield.health import exit_code_for
from almc_shield.shield.render import render_snapshot
from almc_shield.shield.snapshot import gather
from almc_shield.state import State


def _state_path(cfg) -> str:
    return str(Path(cfg.outbox.db_path).parent / "state.db")


def _build_f2b(cfg):
    return F2bClient(jail=cfg.fail2ban.jail_name)


def build_parser() -> argparse.ArgumentParser:
    # Parser plano (un positional `cmd` + flags) para que las opciones funcionen
    # en cualquier orden: `shield status --check -c X` y `shield -c X status`.
    p = argparse.ArgumentParser(prog="shield", description="Panel del agente ALMC Abuse Shield")
    p.add_argument("cmd", nargs="?", default="status", choices=["status"],
                   help="comando (Plan A: solo 'status')")
    p.add_argument("-c", "--config", default="/etc/almc-shield/config.ini")
    p.add_argument("-n", "--interval", type=int, default=None, help="refresco TUI (seg)")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--rows", type=int, default=None)
    p.add_argument("--source", choices=["tenant", "global", "all"], default="all")
    p.add_argument("--once", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--check", action="store_true")
    return p


def _gather(cfg):
    state = State(_state_path(cfg))
    outbox = Outbox(cfg.outbox.db_path, cfg.outbox.max_size_mb, cfg.outbox.max_age_days)
    return gather(cfg, state, outbox, _build_f2b(cfg))


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except Exception as e:
        print(f"FATAL: no se pudo cargar config {args.config}: {e}", file=sys.stderr)
        return 2

    snap = _gather(cfg)
    rows = args.rows if args.rows is not None else cfg.shield.rows

    # --check : sin UI, solo exit code (0/1/2)
    if args.check:
        return exit_code_for(snap.status)

    # --json : salida máquina
    if args.json:
        print(json.dumps(dataclasses.asdict(snap), default=str))
        return 0

    # default y `status [--once]`
    # (Plan A: siempre one-shot; el modo interactivo TUI llega en Plan B)
    # Salida robusta en terminales/locales no-UTF8 (evita crash al pintar glifos).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    from rich.console import Console
    color_system = None if (args.no_color or cfg.shield.color == "never") else "auto"
    Console(color_system=color_system).print(render_snapshot(snap, rows=rows, source=args.source))
    return 0


if __name__ == "__main__":
    sys.exit(main())
