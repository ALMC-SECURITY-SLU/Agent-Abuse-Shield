"""Tests for circuit_breaker.py."""
import time

import pytest

from almc_shield.circuit_breaker import CircuitBreaker, State


def test_starts_closed() -> None:
    cb = CircuitBreaker(failures_to_open=3, cooldown_seconds=1, cooldown_cap=10)
    assert cb.state == State.CLOSED
    assert cb.allow() is True


def test_opens_after_n_failures() -> None:
    cb = CircuitBreaker(failures_to_open=3, cooldown_seconds=1, cooldown_cap=10)
    for _ in range(2):
        cb.record_failure()
    assert cb.state == State.CLOSED
    cb.record_failure()
    assert cb.state == State.OPEN
    assert cb.allow() is False


def test_half_open_after_cooldown() -> None:
    cb = CircuitBreaker(failures_to_open=2, cooldown_seconds=0, cooldown_cap=10)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == State.OPEN
    time.sleep(0.01)
    # cooldown=0 -> immediately HALF_OPEN
    assert cb.allow() is True
    assert cb.state == State.HALF_OPEN


def test_half_open_success_closes() -> None:
    cb = CircuitBreaker(failures_to_open=2, cooldown_seconds=0, cooldown_cap=10)
    cb.record_failure()
    cb.record_failure()
    cb.allow()    # transitions to HALF_OPEN
    cb.record_success()
    assert cb.state == State.CLOSED


def test_half_open_failure_reopens_with_longer_cooldown() -> None:
    cb = CircuitBreaker(failures_to_open=2, cooldown_seconds=1, cooldown_cap=10)
    cb.record_failure()
    cb.record_failure()
    assert cb.cooldown_seconds == 1
    cb._open_until = time.time() - 1   # force expire
    cb.allow()    # HALF_OPEN
    cb.record_failure()
    assert cb.state == State.OPEN
    # Cooldown doubled
    assert cb.cooldown_seconds == 2


def test_cooldown_capped() -> None:
    cb = CircuitBreaker(failures_to_open=1, cooldown_seconds=4, cooldown_cap=5)
    cb.record_failure()    # -> OPEN, cooldown=4
    cb._open_until = time.time() - 1
    cb.allow()             # -> HALF_OPEN
    cb.record_failure()    # -> OPEN, cooldown=5 (capped)
    assert cb.cooldown_seconds == 5
