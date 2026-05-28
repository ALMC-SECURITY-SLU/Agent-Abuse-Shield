"""Puller: GET /blocklist?since=cursor periodically, apply diff to fail2ban."""
from __future__ import annotations
import threading
import time
import structlog
from almc_shield.config import Config
from almc_shield.f2b_client import F2bClient
from almc_shield.state import State

log = structlog.get_logger(__name__)


class Puller(threading.Thread):
    def __init__(self, cfg: Config, state: State, http_client, f2b: F2bClient):
        super().__init__(daemon=True, name="puller")
        self.cfg = cfg
        self.state = state
        self.client = http_client
        self.f2b = f2b
        self._stop_event = threading.Event()
        # Tier from heartbeat response (placeholder True for now; F11 will gate it via tenant settings)
        self.include_global = True

    def stop(self):
        self._stop_event.set()

    def run(self):
        log.info("puller_starting", interval=self.cfg.puller.interval_seconds)
        # Initial: detect cold boot or fail2ban restart, do full sync if needed
        self._maybe_full_sync()
        while not self._stop_event.is_set():
            try:
                self.pull_once()
            except Exception as e:
                log.warning("puller_error", error=str(e))
            self._stop_event.wait(timeout=self.cfg.puller.interval_seconds)
        log.info("puller_stopped")

    def _maybe_full_sync(self):
        """Decide if a full sync is needed.

        Triggers a FULL sync of the central blocklist (vs the delta sync) in any
        of these situations:

        1. **Cold boot real**: el state.db local no tiene IPs aplicadas todavía
           (`applied_ips` vacío). Sucede tras instalar el agente por primera vez
           o tras un reset manual de state.db.

        2. **PID snapshot ausente**: nunca habíamos visto un PID de fail2ban (probable
           combinación de boot frío + entorno donde fail2ban no escribe pid file —
           p. ej. Debian 12 con systemd nativo no escribe `/var/run/fail2ban/fail2ban.pid`).

        3. **PID cambió**: indica que fail2ban se reinició → re-hidratación.

        El detalle clave (bug fix 2026-05-27): antes la primera condición usaba el
        PID como gating, y si PID es None se hacía warning sin marcar `need_full`.
        Eso dejaba al puller esperando a un delta que nunca llegaba (cursor ya estaba
        más allá del último id del blocklist tras el primer pull) → las IPs se
        consumían en el delta pero el agente nunca las aplicaba realmente.
        """
        pid = self.f2b.server_pid()
        snap = self.state.get_pid_snapshot()
        applied_count = self.state.count_applied()

        need_full = False
        reason = None

        if applied_count == 0:
            need_full = True
            reason = "first_boot_no_applied_ips"
        elif snap is None and pid is not None:
            need_full = True
            reason = "first_pid_snapshot"
        elif snap is not None and pid is not None and pid != snap:
            need_full = True
            reason = f"pid_changed_old_{snap}_new_{pid}"
        elif pid is None:
            log.warning("fail2ban_pid_missing",
                        note="cannot detect future restarts via PID; relying on canary heartbeat")

        if need_full:
            log.info("full_sync_triggered", reason=reason,
                     applied_count_before=applied_count)
            self._full_sync()
            if pid:
                self.state.set_pid_snapshot(pid)

    def _full_sync(self):
        try:
            r = self.client.get("/blocklist/full", params={
                "include_global": "true" if self.include_global else "false",
                "page": 1,
            }, timeout=30.0)
            if r.status_code != 200:
                log.warning("full_sync_http_error", status=r.status_code)
                return
            data = r.json()
            items = data.get("items", [])
            applied = 0
            for item in items:
                ip = item.get("ip")
                if not ip:
                    continue
                if self.f2b.banip(ip):
                    self.state.add_applied(ip, item.get("source", "tenant"))
                    applied += 1
                time.sleep(0.2)  # Don't saturate fail2ban
            log.info("full_sync_applied", count=applied)
        except Exception as e:
            log.warning("full_sync_failed", error=str(e))

    def pull_once(self):
        cursor = self.state.get_cursor()
        try:
            r = self.client.get("/blocklist", params={
                "since": cursor,
                "include_global": "true" if self.include_global else "false",
                "max": 10000,
            })
        except Exception as e:
            log.warning("pull_request_failed", error=str(e))
            return

        if r.status_code != 200:
            log.warning("pull_http_error", status=r.status_code, body=r.text[:200])
            return

        data = r.json()
        items = data.get("items", [])
        new_cursor = int(data.get("cursor", cursor))

        added = removed = 0
        for item in items:
            ip = item.get("ip")
            op = item.get("op")
            if not ip:
                continue
            if op == "add":
                if self.f2b.banip(ip):
                    self.state.add_applied(ip, item.get("source", "tenant"))
                    added += 1
            elif op == "remove":
                if self.f2b.unbanip(ip):
                    self.state.remove_applied(ip)
                    removed += 1

        if new_cursor > cursor:
            self.state.set_cursor(new_cursor)
        if items:
            log.info("pull_applied", added=added, removed=removed, cursor_from=cursor, cursor_to=new_cursor)
