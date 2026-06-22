"""Sender: drains outbox -> POST /report.

Single-threaded loop. Uses CircuitBreaker to back off on persistent failures.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from typing import Optional

import structlog

from almc_shield.circuit_breaker import CircuitBreaker
from almc_shield.config import Config
from almc_shield.outbox import Outbox
from almc_shield.version import __version__

log = structlog.get_logger(__name__)


class Sender:
    def __init__(self, cfg: Config, outbox: Outbox, hostname: str, http_client) -> None:
        self.cfg = cfg
        self.outbox = outbox
        self.hostname = hostname
        self.client = http_client
        self.breaker = CircuitBreaker(
            failures_to_open=cfg.sender.circuit_breaker_failures_to_open,
            cooldown_seconds=cfg.sender.circuit_breaker_cooldown_seconds,
            cooldown_cap=cfg.sender.circuit_breaker_cooldown_cap,
        )
        # Sticky flags
        self.auth_blocked = False
        self.tenant_suspended = False
        self.last_status: Optional[int] = None
        self.last_error: Optional[str] = None

    def flush_once(self) -> bool:
        """Try to send one batch. Returns True if anything was processed (success or dropped)."""
        if self.auth_blocked or self.tenant_suspended:
            return False
        if not self.breaker.allow():
            return False

        batch = self.outbox.fetch_batch(self.cfg.outbox.batch_size_max)
        if not batch:
            return False

        ids = [bid for bid, _ in batch]
        bans = [be.to_dict() for _, be in batch]

        # Deterministic idempotency key derived from the row IDs in the batch.
        # If the agent crashes after the server received the request but BEFORE
        # we delete the rows from the outbox, the retry must reuse the same key
        # so the server's idempotency cache (24h Redis) deduplicates.
        # Format: UUID-style 36 chars, derived from sha256 of sorted ids.
        canonical = ",".join(str(i) for i in sorted(ids))
        digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        # First 32 hex chars → UUID-shaped 8-4-4-4-12
        idempotency_key = (
            digest[:8] + "-" + digest[8:12] + "-" + digest[12:16]
            + "-" + digest[16:20] + "-" + digest[20:32]
        )

        payload = {
            "agent_version": __version__,
            "hostname": self.hostname,
            "bans": bans,
        }

        try:
            resp = self.client.post(
                "/report",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.cfg.api.api_key}",
                    "Idempotency-Key": idempotency_key,
                    "Content-Type": "application/json",
                },
            )
        except Exception as e:
            self.breaker.record_failure()
            self.last_error = type(e).__name__ + ": " + str(e)
            log.warning("send_failed_network", error=self.last_error, batch_size=len(bans))
            time.sleep(self._backoff_sleep())
            return False

        self.last_status = resp.status_code

        if 200 <= resp.status_code < 300:
            # Success — drop from outbox
            self.outbox.delete_batch(ids)
            self.breaker.record_success()
            self.last_error = None
            log.info("send_ok", batch_size=len(bans), status=resp.status_code)
            return True

        if resp.status_code == 401:
            self.auth_blocked = True
            self.last_error = "invalid_api_key"
            log.error("send_auth_failed_blocking", batch_size=len(bans))
            return False

        if resp.status_code == 403:
            body = self._safe_json(resp)
            err = body.get("error", "forbidden") if body else "forbidden"
            if err == "tenant_suspended":
                self.tenant_suspended = True
                log.error("send_tenant_suspended_blocking", batch_size=len(bans))
                return False
            if err == "plan_quota_exceeded":
                # Don't drop, but back off heavily
                self.breaker.record_failure()
                log.warning("send_quota_exceeded", batch_size=len(bans))
                time.sleep(min(self.cfg.sender.backoff_max * 5, 900))
                return False

        if resp.status_code == 429:
            # Respect Retry-After if present
            retry_after = self._parse_retry_after(resp.headers.get("Retry-After"))
            if retry_after is None:
                retry_after = max(self.cfg.sender.backoff_max, 60)
            log.warning("send_rate_limited", retry_after_s=retry_after, batch_size=len(bans))
            time.sleep(retry_after)
            self.breaker.record_failure()
            return False

        if 400 <= resp.status_code < 500:
            # Validation / bad request — drop batch (it'd never succeed) but flag for review
            log.error("send_bad_payload_dropping_batch", status=resp.status_code, body=resp.text[:500])
            self.outbox.delete_batch(ids)
            return True

        # 5xx — server error
        self.breaker.record_failure()
        self.last_error = f"http_{resp.status_code}"
        log.warning("send_5xx_retry", status=resp.status_code, batch_size=len(bans))
        time.sleep(self._backoff_sleep())
        return False

    def _backoff_sleep(self) -> float:
        b = min(self.cfg.sender.backoff_max, self.cfg.sender.backoff_min * (2 ** min(self.breaker._consecutive_failures, 6)))
        if self.cfg.sender.backoff_jitter:
            b = b * (0.5 + random.random())
        return b

    @staticmethod
    def _parse_retry_after(value: Optional[str]) -> Optional[int]:
        if not value:
            return None
        try:
            return max(0, int(value))
        except ValueError:
            return None

    @staticmethod
    def _safe_json(resp) -> dict:
        try:
            return resp.json() or {}
        except Exception:
            return {}
