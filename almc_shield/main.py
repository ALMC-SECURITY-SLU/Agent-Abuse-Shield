"""Main agent orchestrator."""
from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import structlog

from almc_shield import logging_setup
from almc_shield.config import Config, load as load_config
from almc_shield.env_detect import detect_environment
from almc_shield.f2b_client import F2bClient
from almc_shield.heartbeat import Heartbeat
from almc_shield.http_client import build_client
from almc_shield.outbox import Outbox
from almc_shield.puller import Puller
from almc_shield.reader import Reader
from almc_shield.sender import Sender
from almc_shield.state import State
from almc_shield.version import __version__

log = structlog.get_logger(__name__)


def _default_state_path(outbox_db_path: str) -> str:
    """Derive state.db path sibling to outbox.db.

    /var/lib/almc-shield/outbox.db -> /var/lib/almc-shield/state.db
    """
    p = Path(outbox_db_path).parent / "state.db"
    return str(p)


def build_status_snapshot(agent) -> dict:
    """Construye el dict de estado que el agente persiste para que `shield` lo lea."""
    threads = {
        "reader": agent.reader.is_alive(),
        "sender": True,  # sender corre en el bucle principal, no es hilo propio
        "puller": agent.puller.is_alive(),
        "heartbeat": agent.heartbeat.is_alive(),
    }
    if not all(threads.values()):
        status = "critical"
    elif agent.heartbeat.degraded:
        status = "degraded"
    else:
        status = "healthy"
    return {
        "started_at": getattr(agent.heartbeat, "_start_time", None),
        "hb_last_at": getattr(agent.heartbeat, "last_ok_at", None),
        "hb_ok": not agent.heartbeat.degraded,
        "status": status,
        "threads": threads,
        "last_pull_at": getattr(agent.puller, "last_pull_at", None),
        "last_flush_at": getattr(agent, "_last_flush_at", None),
    }


class Agent:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.hostname = socket.gethostname()
        self.outbox = Outbox(cfg.outbox.db_path, cfg.outbox.max_size_mb, cfg.outbox.max_age_days)
        self.state = State(_default_state_path(cfg.outbox.db_path))
        self.http_client = build_client(cfg)
        self.f2b = F2bClient(jail=cfg.fail2ban.jail_name)
        self.sender = Sender(cfg, self.outbox, self.hostname, self.http_client)
        self.reader = Reader(cfg.fail2ban.log_path, self.outbox)
        self.puller = Puller(cfg, self.state, self.http_client, self.f2b)
        self.heartbeat = Heartbeat(cfg, self.outbox, self.state, self.sender, self.http_client)
        self.heartbeat._start_time = time.time()
        self._stop_event = threading.Event()
        self._last_flush_at = None

    def run(self) -> int:
        env = detect_environment()
        log.info("agent_starting", version=__version__, hostname=self.hostname, environment=env,
                 api_url=self.cfg.api.url)

        # Install signal handlers
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)

        self.reader.start()
        self.puller.start()
        self.heartbeat.start()

        last_flush = 0.0
        try:
            while not self._stop_event.is_set():
                now = time.time()
                interval = self.cfg.sender.flush_interval_seconds
                # Adaptive: flush sooner if outbox is large
                if self.outbox.size() >= self.cfg.sender.on_demand_threshold:
                    interval = 1
                if now - last_flush >= interval:
                    self.sender.flush_once()
                    last_flush = now
                    self._last_flush_at = now
                # TTL/size eviction once a minute
                if int(now) % 60 == 0:
                    self.outbox.drop_oldest_if_over_quota()
                    self.outbox.drop_older_than(self.cfg.outbox.max_age_days * 86400)
                # Status snapshot para `shield` (cada ~5s)
                if int(now) % 5 == 0:
                    try:
                        snap = build_status_snapshot(self)
                        snap["snapshot_at"] = now
                        self.state.set_snapshot(snap)
                    except Exception:
                        pass
                self._stop_event.wait(timeout=1.0)
        finally:
            log.info("agent_stopping_drain_outbox")
            # SIGTERM stop order (per F4 spec):
            # 1. heartbeat first -> send final degraded state to backend
            # 2. puller -> stop pulling new blocklist deltas
            # 3. reader -> stop tailing fail2ban.log
            # 4. final sender flush -> drain outbox
            self.heartbeat.degraded = True
            try:
                self.heartbeat.send_once()
            except Exception as e:
                log.warning("final_heartbeat_failed", error=str(e))
            self.heartbeat.stop()
            self.heartbeat.join(timeout=5)

            self.puller.stop()
            self.puller.join(timeout=5)

            self.reader.stop()
            self.reader.join(timeout=10)

            # Final flush attempts (max 5)
            for _ in range(5):
                if self.outbox.size() == 0:
                    break
                if not self.sender.flush_once():
                    break

            self.http_client.close()
            log.info("agent_stopped")
        return 0

    def _on_signal(self, signum, frame) -> None:
        log.info("signal_received", signum=signum)
        self._stop_event.set()


def main() -> int:
    parser = argparse.ArgumentParser(description="ALMC Abuse Shield agent")
    parser.add_argument("--config", "-c", default="/etc/almc-shield/config.ini",
                        help="Path to config.ini")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
    except Exception as e:
        print(f"FATAL: failed to load config {args.config}: {e}", file=sys.stderr)
        return 2

    logging_setup.configure(
        level=cfg.logging.level,
        file_path=cfg.logging.file,
        destination=cfg.runtime.log_destination,
        max_size_mb=cfg.logging.max_size_mb,
        backup_count=cfg.logging.backup_count,
    )

    agent = Agent(cfg)
    return agent.run()


if __name__ == "__main__":
    sys.exit(main())
