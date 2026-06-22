"""CLI `shield`: visor + acciones de control del agente."""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from almc_shield.config import load as load_config
from almc_shield.f2b_client import F2bClient
from almc_shield.outbox import Outbox
from almc_shield.shield import actions
from almc_shield.shield.health import exit_code_for
from almc_shield.shield.render import render_snapshot
from almc_shield.shield.snapshot import gather
from almc_shield.state import State

_ACTIONS = ("update", "disable", "enable", "feed-global", "uninstall")


def _state_path(cfg) -> str:
    return str(Path(cfg.outbox.db_path).parent / "state.db")


def _build_f2b(cfg):
    return F2bClient(jail=cfg.fail2ban.jail_name)


def build_parser() -> argparse.ArgumentParser:
    # Parser plano (positional `cmd` + flags) para que las opciones funcionen
    # en cualquier orden: `shield status --check -c X` y `shield -c X status`.
    p = argparse.ArgumentParser(prog="shield", description="Panel y control del agente ALMC Abuse Shield")
    p.add_argument("cmd", nargs="?", default=None,
                   choices=["status", *_ACTIONS],
                   help="status (default) | " + " | ".join(_ACTIONS))
    p.add_argument("arg", nargs="?", default=None, help="on|off (para feed-global)")
    p.add_argument("-c", "--config", default="/etc/almc-shield/config.ini")
    p.add_argument("-n", "--interval", type=int, default=None, help="refresco TUI (seg)")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--rows", type=int, default=None)
    p.add_argument("--source", choices=["tenant", "global", "all"], default="all")
    p.add_argument("--once", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("-y", "--yes", action="store_true", help="no pedir confirmación")
    return p


def _gather(cfg):
    state = State(_state_path(cfg))
    outbox = Outbox(cfg.outbox.db_path, cfg.outbox.max_size_mb, cfg.outbox.max_age_days)
    return gather(cfg, state, outbox, _build_f2b(cfg))


def _dispatch_action(cmd, args, cfg) -> int:
    if cmd == "update":
        return actions.update(cfg, args.config, assume_yes=args.yes)
    if cmd == "disable":
        return actions.disable(cfg, assume_yes=args.yes)
    if cmd == "enable":
        return actions.enable(cfg)
    if cmd == "uninstall":
        return actions.uninstall(cfg, assume_yes=args.yes)
    if cmd == "feed-global":
        if args.arg not in ("on", "off"):
            print("Uso: shield feed-global on|off", file=sys.stderr)
            return 2
        return actions.feed_global(
            cfg, args.config, turn_on=(args.arg == "on"),
            state=State(_state_path(cfg)), f2b=_build_f2b(cfg), assume_yes=args.yes,
        )
    return 2  # no alcanzable


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except Exception as e:
        print(f"FATAL: no se pudo cargar config {args.config}: {e}", file=sys.stderr)
        return 2

    # Acciones de control
    if args.cmd in _ACTIONS:
        return _dispatch_action(args.cmd, args, cfg)

    rows = args.rows if args.rows is not None else cfg.shield.rows

    # TUI interactiva: `shield` a secas en una terminal (no para status/--json/--check)
    from almc_shield.shield import tui
    if tui._should_use_tui(args.cmd, sys.stdout.isatty()) and not args.json and not args.check:
        interval = args.interval if args.interval is not None else cfg.shield.interval_seconds
        chosen = tui.run_tui(lambda: _gather(cfg), cfg.puller.include_global,
                             interval=interval, rows=rows, source=args.source)
        if chosen:
            args.arg = chosen[1]
            return _dispatch_action(chosen[0], args, cfg)
        return 0

    # Vista de estado one-shot (status / --json / --check / sin TTY)
    snap = _gather(cfg)

    if args.check:
        return exit_code_for(snap.status)

    if args.json:
        print(json.dumps(dataclasses.asdict(snap), default=str))
        return 0

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
