"""TUI interactiva en vivo para shield (rich.Live + teclas).

El bucle es glue (smoke en Docker); la lógica testeable son los helpers puros
`_should_use_tui` y `_key_to_action`. Las acciones NO se ejecutan dentro del
Live: al pulsar su tecla, el TUI sale y devuelve (cmd, arg) para que el llamador
las ejecute en una terminal limpia (con su confirmación normal).
"""
from __future__ import annotations

import contextlib
import select
import sys
import time

from almc_shield.shield.render import render_snapshot

# tecla -> (cmd, arg). 'g' (feed-global) se resuelve según el estado actual.
_ACTION_KEYS = {"u": ("update", None), "d": ("disable", None), "x": ("uninstall", None)}


def _should_use_tui(cmd, isatty: bool) -> bool:
    return cmd is None and bool(isatty)


def _key_to_action(key: str, feed_on: bool):
    key = (key or "").lower()
    if key == "g":
        return ("feed-global", "off" if feed_on else "on")
    return _ACTION_KEYS.get(key)


@contextlib.contextmanager
def _raw_terminal():
    """Modo cbreak en POSIX para leer teclas sueltas; no-op si no hay termios."""
    try:
        import termios
        import tty
    except Exception:
        yield
        return
    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except Exception:
        yield
        return
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_key(timeout: float):
    try:
        r, _, _ = select.select([sys.stdin], [], [], timeout)
    except Exception:
        time.sleep(timeout)
        return None
    if r:
        return sys.stdin.read(1)
    return None


def run_tui(gather_fn, feed_on: bool, *, interval: int = 2, rows: int = 10, source: str = "all"):
    """Vista en vivo. Devuelve (cmd, arg) si el usuario pidió una acción, o None al salir."""
    from rich.console import Console
    from rich.live import Live

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    console = Console()
    chosen = None
    with _raw_terminal():
        with Live(render_snapshot(gather_fn(), rows=rows, source=source),
                  console=console, screen=True, refresh_per_second=4, transient=True) as live:
            last = time.time()
            while True:
                key = _read_key(0.25)
                if key:
                    low = key.lower()
                    if low == "q":
                        break
                    if low == "r":
                        last = 0
                        continue
                    act = _key_to_action(key, feed_on)
                    if act:
                        chosen = act
                        break
                now = time.time()
                if now - last >= interval:
                    live.update(render_snapshot(gather_fn(), rows=rows, source=source))
                    last = now
    return chosen
