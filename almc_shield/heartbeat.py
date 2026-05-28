"""Heartbeat: POST /heartbeat with status stats periodically."""
from __future__ import annotations
import socket
import threading
import time
import structlog
from almc_shield.config import Config
from almc_shield.outbox import Outbox
from almc_shield.state import State
from almc_shield.version import __version__

log = structlog.get_logger(__name__)


class Heartbeat(threading.Thread):
    def __init__(self, cfg: Config, outbox: Outbox, state: State, sender, http_client):
        super().__init__(daemon=True, name="heartbeat")
        self.cfg = cfg
        self.outbox = outbox
        self.state = state
        self.sender = sender
        self.client = http_client
        self._stop_event = threading.Event()
        self.degraded = False
        self.hostname = socket.gethostname()

    def stop(self):
        self._stop_event.set()

    def run(self):
        log.info("heartbeat_starting")
        while not self._stop_event.is_set():
            try:
                self.send_once()
            except Exception as e:
                log.warning("heartbeat_error", error=str(e))
                self.degraded = True
            interval = (self.cfg.heartbeat.interval_seconds_degraded if self.degraded
                        else self.cfg.heartbeat.interval_seconds)
            self._stop_event.wait(timeout=interval)
        log.info("heartbeat_stopped")

    def send_once(self):
        payload = {
            "agent_version": __version__,
            "hostname": self.hostname,
            "uptime_seconds": int(time.time() - getattr(self, "_start_time", time.time())),
            "stats": {
                "local_bans": self.state.count_applied(),
                "queue_pending": self.outbox.size(),
                "queue_oldest_seconds": self.outbox.oldest_age_seconds(),
                "last_pull_cursor": self.state.get_cursor(),
                "last_pull_status": "ok" if not self.degraded else "degraded",
                "flags": {
                    "auth_blocked": getattr(self.sender, "auth_blocked", False),
                    "tenant_suspended": getattr(self.sender, "tenant_suspended", False),
                },
            },
        }
        r = self.client.post("/heartbeat", json=payload, timeout=10.0)
        if 200 <= r.status_code < 300:
            self.degraded = False
        elif r.status_code in (401, 403):
            self.degraded = True
            log.warning("heartbeat_auth_issue", status=r.status_code)
        else:
            self.degraded = True
            log.warning("heartbeat_http_error", status=r.status_code)
