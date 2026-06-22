"""Acciones de control de shield: update/disable/enable/feed-global/uninstall.

Cada acción requiere root, las destructivas piden confirmación escrita (salvo
assume_yes) y reportan el evento al backend best-effort (nunca bloquea).
El runner de subprocess y el reporter se inyectan para poder testear sin tocar
el sistema real.
"""
from __future__ import annotations

import configparser
import os
import socket
import subprocess

import httpx
import structlog

from almc_shield.version import __version__

log = structlog.get_logger(__name__)

INSTALL_URL = "https://almc.es/abuse-shield/install.sh"
UNINSTALL_URL = "https://almc.es/abuse-shield/uninstall.sh"
SERVICE = "almc-shield"


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def confirm(prompt: str, assume_yes: bool, _input=input) -> bool:
    if assume_yes:
        return True
    try:
        ans = _input(f"{prompt}\n  Escribe 'si' para confirmar: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("si", "sí", "s", "yes", "y")


def report_event(cfg, event: str, hostname: str | None = None) -> None:
    """Best-effort: avisa al backend del evento. Nunca lanza."""
    try:
        httpx.post(
            f"{cfg.api.url.rstrip('/')}/install-event",
            headers={"Authorization": f"Bearer {cfg.api.api_key}"},
            json={"event": event, "hostname": hostname or socket.gethostname(),
                  "agent_version": __version__},
            timeout=3.0,
        )
    except Exception as e:  # nunca bloquear la acción por un fallo de audit
        log.warning("audit_report_failed", evt=event, error=str(e))


def _read_api_key(config_path: str) -> str:
    cp = configparser.ConfigParser()
    cp.read(config_path, encoding="utf-8")
    return cp.get("api", "api_key", fallback="").strip()


def _set_include_global(config_path: str, value: bool) -> None:
    cp = configparser.ConfigParser()
    cp.read(config_path, encoding="utf-8")
    if not cp.has_section("puller"):
        cp.add_section("puller")
    cp.set("puller", "include_global", "true" if value else "false")
    with open(config_path, "w", encoding="utf-8") as f:
        cp.write(f)


def _need_root(action: str) -> bool:
    if not is_root():
        print(f"Requiere root: usa 'sudo shield {action}'")
        return False
    return True


def disable(cfg, *, assume_yes=False, run=subprocess.run, report=report_event) -> int:
    if not _need_root("disable"):
        return 2
    if not confirm("Vas a DESHABILITAR el agente (deja de actualizar la protección; "
                   "los bloqueos actuales del jail expiran solos).", assume_yes):
        print("Cancelado.")
        return 1
    run(["systemctl", "disable", "--now", SERVICE])
    report(cfg, "disabled")
    print("Agente deshabilitado.")
    return 0


def enable(cfg, *, run=subprocess.run, report=report_event) -> int:
    if not _need_root("enable"):
        return 2
    run(["systemctl", "enable", "--now", SERVICE])
    report(cfg, "enabled")
    print("Agente habilitado.")
    return 0


def update(cfg, config_path, *, assume_yes=False, run=subprocess.run, report=report_event) -> int:
    if not _need_root("update"):
        return 2
    key = _read_api_key(config_path)
    if not key:
        print("No se pudo leer la api_key de la config.")
        return 2
    cmd = ["bash", "-c",
           f'curl -fsSL {INSTALL_URL} | ABUSE_SHIELD_API_KEY="{key}" bash -s -- --reinstall']
    result = run(cmd)
    rc = getattr(result, "returncode", 0)
    report(cfg, "updated")
    if rc != 0:
        print(f"La actualización terminó con código {rc}.")
        return rc
    print("Actualización lanzada.")
    return 0


def uninstall(cfg, *, assume_yes=False, run=subprocess.run, report=report_event) -> int:
    if not _need_root("uninstall"):
        return 2
    if not confirm("Vas a DESINSTALAR el agente por completo.", assume_yes):
        print("Cancelado.")
        return 1
    report(cfg, "uninstalled")  # reportar ANTES de borrarse
    run(["bash", "-c", f"curl -fsSL {UNINSTALL_URL} | bash"])
    print("Agente desinstalado.")
    return 0


def feed_global(cfg, config_path, turn_on: bool, *, state, f2b, assume_yes=False,
                run=subprocess.run, report=report_event) -> int:
    if not _need_root("feed-global"):
        return 2
    if turn_on:
        _set_include_global(config_path, True)
        run(["systemctl", "restart", SERVICE])
        report(cfg, "feed_global_on")
        print("Reconectado al feed global; se re-aplicará en el próximo pull.")
        return 0
    globals_ = state.applied_by_source("global")
    if not confirm(f"Vas a DESCONECTAR del feed global. Se quitarán {len(globals_)} IPs del "
                   "feed abuse del jail; quedarás protegido solo con tus propios bans.", assume_yes):
        print("Cancelado.")
        return 1
    _set_include_global(config_path, False)
    removed = 0
    for ip in globals_:
        if f2b.unbanip(ip):
            state.remove_applied(ip)
            removed += 1
    run(["systemctl", "restart", SERVICE])
    report(cfg, "feed_global_off")
    print(f"Desconectado del feed global. {removed} IPs quitadas.")
    return 0
