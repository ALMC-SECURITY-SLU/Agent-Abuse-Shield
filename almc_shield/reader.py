"""Reader: tails /var/log/fail2ban.log and enqueues BanEvents.

Uses watchdog (inotify on Linux) for change notifications + manual seek-to-end
on rotation. Keeps a file offset to avoid re-reading lines after agent restart.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Optional

import structlog
from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from almc_shield.outbox import Outbox
from almc_shield.parser import parse_ban_line

log = structlog.get_logger(__name__)


class Reader(threading.Thread):
    def __init__(self, log_path: str, outbox: Outbox, state_offset_file: Optional[str] = None) -> None:
        super().__init__(daemon=True, name="reader")
        self.log_path = log_path
        self.outbox = outbox
        self.state_offset_file = state_offset_file or "/var/lib/almc-shield/reader_offset"
        self._stop_event = threading.Event()
        self._offset = self._load_offset()
        self._lock = threading.Lock()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        log.info("reader_starting", log_path=self.log_path, offset=self._offset)
        # Drain any pending lines on startup
        self._read_new_lines()

        observer = Observer()
        observer.schedule(_Handler(self._on_modified), str(Path(self.log_path).parent), recursive=False)
        observer.start()

        try:
            while not self._stop_event.is_set():
                # Periodic safety poll in case inotify misses an event
                self._read_new_lines()
                self._stop_event.wait(timeout=2.0)
        finally:
            observer.stop()
            observer.join(timeout=5)
            log.info("reader_stopped")

    def _on_modified(self, path: str) -> None:
        if os.path.realpath(path) == os.path.realpath(self.log_path):
            self._read_new_lines()

    def _read_new_lines(self) -> None:
        with self._lock:
            try:
                size = os.path.getsize(self.log_path)
            except OSError:
                return

            if size < self._offset:
                # Log was rotated/truncated. Reset offset to 0.
                log.info("reader_log_rotated", new_size=size, old_offset=self._offset)
                self._offset = 0

            if size == self._offset:
                return

            try:
                with open(self.log_path, "rb") as f:
                    f.seek(self._offset)
                    chunk = f.read(size - self._offset)
                    self._offset = f.tell()
            except OSError as e:
                log.warning("reader_read_failed", error=str(e))
                return

            # Process line by line (UTF-8 best-effort)
            try:
                text = chunk.decode("utf-8", errors="replace")
            except Exception:
                return

            new_events = 0
            for line in text.splitlines():
                event = parse_ban_line(line)
                if event is None:
                    continue
                try:
                    self.outbox.enqueue(event)
                    new_events += 1
                except Exception as e:
                    log.warning("reader_enqueue_failed", error=str(e), ip=event.ip)

            if new_events > 0:
                log.info("reader_new_bans", count=new_events)

            self._save_offset()

    def _load_offset(self) -> int:
        """Carga el offset persistido del último read.

        Si NO hay state file previo (cold boot en host nuevo), arranca en EOF
        del log para evitar leer y enviar bans históricos ya procesados por
        fail2ban (que sería 100-500MB de log en hosts maduros → OOM peak).
        Solo nos importan los bans NUEVOS desde que arrancamos.

        En reinicio del agente, el state file existe → no cold boot.
        En rotación del log, se detecta por size < offset → reset a 0.
        """
        try:
            p = Path(self.state_offset_file)
            if p.exists():
                return int(p.read_text().strip())
        except (OSError, ValueError):
            pass
        # Cold boot: arrancar en EOF para no procesar log histórico
        try:
            eof = os.path.getsize(self.log_path)
            log.info("reader_cold_boot_seek_eof", offset=eof, log_path=self.log_path)
            return eof
        except OSError:
            return 0

    def _save_offset(self) -> None:
        try:
            p = Path(self.state_offset_file)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(self._offset))
        except OSError as e:
            log.warning("reader_offset_save_failed", error=str(e))


class _Handler(FileSystemEventHandler):
    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def on_modified(self, event: FileModifiedEvent) -> None:
        if event.is_directory:
            return
        self._callback(event.src_path)

    def on_created(self, event) -> None:
        # File created/recreated after rotation
        if event.is_directory:
            return
        self._callback(event.src_path)
