"""Puller: GET /blocklist?since=cursor periodically, apply diff to fail2ban."""
from __future__ import annotations
import threading
import time
import structlog
from almc_shield.config import Config
from almc_shield.f2b_client import F2bClient
from almc_shield.state import State

log = structlog.get_logger(__name__)

MAX_PULL_PAGES_PER_CYCLE = 50  # tope anti-bucle del delta (~100K IPs/ciclo)
MAX_FULL_PAGES = 100           # tope anti-bucle del full sync (~500K IPs)


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
        page = 1
        applied = 0
        while page is not None and page <= MAX_FULL_PAGES:
            try:
                r = self.client.get("/blocklist/full", params={
                    "include_global": "true" if self.include_global else "false",
                    "page": page,
                }, timeout=30.0)
            except Exception as e:
                log.warning("full_sync_failed", error=str(e), page=page)
                return
            if r.status_code != 200:
                log.warning("full_sync_http_error", status=r.status_code, page=page)
                return
            data = r.json()
            items = data.get("items", [])
            for item in items:
                ip = item.get("ip")
                if not ip:
                    continue
                if self.f2b.banip(ip):
                    self.state.add_applied(ip, item.get("source", "tenant"))
                    applied += 1
                time.sleep(0.2)  # Don't saturate el backend de bloqueo
            page = data.get("next_page")
        log.info("full_sync_applied", count=applied)

    def pull_once(self):
        cursor = self.state.get_cursor()
        global_cursor = self.state.get_global_cursor()
        added = removed = 0

        for _ in range(MAX_PULL_PAGES_PER_CYCLE):
            try:
                r = self.client.get("/blocklist", params={
                    "since": cursor,
                    "global_since": global_cursor,
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
            new_global_cursor = int(data.get("global_cursor", global_cursor))
            tenant_more = data.get("next_cursor") is not None
            global_advanced = new_global_cursor > global_cursor

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

            # Persistir cada cursor tras cada lote: un fallo a mitad reanuda
            # desde donde quedó en vez de re-aplicar todo.
            if new_cursor > cursor:
                cursor = new_cursor
                self.state.set_cursor(cursor)
            if new_global_cursor > global_cursor:
                global_cursor = new_global_cursor
                self.state.set_global_cursor(global_cursor)

            # Drenado cuando el tenant no tiene más páginas y el global no avanzó.
            if not tenant_more and not global_advanced:
                break
        else:
            log.warning("pull_cap_reached", max_pages=MAX_PULL_PAGES_PER_CYCLE,
                        cursor=cursor, global_cursor=global_cursor)

        if added or removed:
            log.info("pull_applied", added=added, removed=removed,
                     cursor=cursor, global_cursor=global_cursor)
