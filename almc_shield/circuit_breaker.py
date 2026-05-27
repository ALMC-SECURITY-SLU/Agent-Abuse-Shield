"""Circuit breaker state machine for the HTTP sender.

States: CLOSED -> OPEN (cooldown) -> HALF_OPEN (probe) -> CLOSED or OPEN(longer).
"""
from __future__ import annotations

import time
from enum import Enum
from threading import Lock


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, failures_to_open: int = 10, cooldown_seconds: int = 60, cooldown_cap: int = 900) -> None:
        self.failures_to_open = failures_to_open
        self.cooldown_seconds = cooldown_seconds
        self.cooldown_cap = cooldown_cap
        self._initial_cooldown = cooldown_seconds
        self._state = State.CLOSED
        self._consecutive_failures = 0
        self._open_until = 0.0
        self._lock = Lock()

    @property
    def state(self) -> State:
        return self._state

    def allow(self) -> bool:
        """Return True if a request may proceed. Side-effect: transitions OPEN->HALF_OPEN if cooldown expired."""
        with self._lock:
            if self._state == State.CLOSED:
                return True
            if self._state == State.HALF_OPEN:
                return True
            # OPEN
            if time.time() >= self._open_until:
                self._state = State.HALF_OPEN
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._state = State.CLOSED
            self._consecutive_failures = 0
            self.cooldown_seconds = self._initial_cooldown

    def record_failure(self) -> None:
        with self._lock:
            if self._state == State.HALF_OPEN:
                # Probe failed -> reopen with doubled cooldown (capped)
                self.cooldown_seconds = min(self.cooldown_seconds * 2, self.cooldown_cap)
                self._state = State.OPEN
                self._open_until = time.time() + self.cooldown_seconds
                return
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failures_to_open:
                self._state = State.OPEN
                self._open_until = time.time() + self.cooldown_seconds
