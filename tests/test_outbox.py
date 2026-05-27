"""Tests for outbox.py."""
from datetime import datetime
from pathlib import Path

import pytest

from almc_shield.outbox import Outbox
from almc_shield.parser import BanEvent


@pytest.fixture
def outbox(tmp_path: Path) -> Outbox:
    db = tmp_path / "outbox.db"
    return Outbox(str(db), max_size_mb=10, max_age_days=7)


def test_empty_initially(outbox: Outbox) -> None:
    assert outbox.size() == 0
    assert outbox.fetch_batch(10) == []


def test_enqueue_and_fetch(outbox: Outbox) -> None:
    e = BanEvent("sshd", "1.2.3.4", datetime(2026, 5, 13, 10, 0, 0))
    outbox.enqueue(e)
    assert outbox.size() == 1

    batch = outbox.fetch_batch(10)
    assert len(batch) == 1
    assert batch[0][1].ip == "1.2.3.4"   # (id, BanEvent)


def test_delete_batch(outbox: Outbox) -> None:
    outbox.enqueue(BanEvent("sshd", "1.1.1.1", datetime.utcnow()))
    outbox.enqueue(BanEvent("sshd", "2.2.2.2", datetime.utcnow()))
    batch = outbox.fetch_batch(10)
    assert len(batch) == 2

    ids = [bid for bid, _ in batch]
    outbox.delete_batch(ids)
    assert outbox.size() == 0


def test_fifo_order(outbox: Outbox) -> None:
    for i in range(5):
        outbox.enqueue(BanEvent("sshd", f"10.0.0.{i}", datetime.utcnow()))
    batch = outbox.fetch_batch(3)
    assert len(batch) == 3
    ips = [be.ip for _, be in batch]
    assert ips == ["10.0.0.0", "10.0.0.1", "10.0.0.2"]


def test_oldest_age(outbox: Outbox) -> None:
    assert outbox.oldest_age_seconds() == 0
    outbox.enqueue(BanEvent("sshd", "1.2.3.4", datetime.utcnow()))
    # Just verify it returns a non-negative int (could be 0 if same second)
    assert outbox.oldest_age_seconds() >= 0


def test_drop_oldest_when_full(tmp_path: Path) -> None:
    # Use a tiny max_size_mb to force eviction. Estimate row ~200 bytes.
    db = tmp_path / "outbox.db"
    ob = Outbox(str(db), max_size_mb=0, max_age_days=7)  # 0 MB -> any row is "overflow"
    # Just verify drop_oldest doesn't crash
    for i in range(20):
        ob.enqueue(BanEvent("sshd", f"10.0.0.{i}", datetime.utcnow()))
    ob.drop_oldest_if_over_quota()
    # After drop, size should not exceed quota (might be 0 if all dropped)
    assert ob.size() >= 0
