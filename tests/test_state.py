"""Tests for state.py — sqlite-backed cursor + applied IPs + PID snapshot."""
from pathlib import Path

import pytest

from almc_shield.state import State


def test_initial_cursor_is_zero(tmp_path: Path) -> None:
    s = State(str(tmp_path / "state.db"))
    assert s.get_cursor() == 0


def test_set_and_get_cursor(tmp_path: Path) -> None:
    s = State(str(tmp_path / "state.db"))
    s.set_cursor(12345)
    assert s.get_cursor() == 12345
    s.set_cursor(67890)
    assert s.get_cursor() == 67890


def test_cursor_persists_across_instances(tmp_path: Path) -> None:
    db = str(tmp_path / "state.db")
    s1 = State(db)
    s1.set_cursor(999)
    s2 = State(db)
    assert s2.get_cursor() == 999


def test_initial_pid_snapshot_is_none(tmp_path: Path) -> None:
    s = State(str(tmp_path / "state.db"))
    assert s.get_pid_snapshot() is None


def test_set_and_get_pid_snapshot(tmp_path: Path) -> None:
    s = State(str(tmp_path / "state.db"))
    s.set_pid_snapshot(1234)
    assert s.get_pid_snapshot() == 1234
    s.set_pid_snapshot(5678)  # update
    assert s.get_pid_snapshot() == 5678


def test_applied_ips_empty(tmp_path: Path) -> None:
    s = State(str(tmp_path / "state.db"))
    assert s.all_applied() == []
    assert s.count_applied() == 0


def test_add_applied_ip(tmp_path: Path) -> None:
    s = State(str(tmp_path / "state.db"))
    s.add_applied("1.2.3.4", "tenant")
    s.add_applied("5.6.7.8", "global")
    assert s.count_applied() == 2
    applied = s.all_applied()
    assert "1.2.3.4" in applied
    assert "5.6.7.8" in applied


def test_remove_applied_ip(tmp_path: Path) -> None:
    s = State(str(tmp_path / "state.db"))
    s.add_applied("1.2.3.4", "tenant")
    s.add_applied("5.6.7.8", "global")
    s.remove_applied("1.2.3.4")
    assert s.count_applied() == 1
    assert s.all_applied() == ["5.6.7.8"]


def test_add_applied_ip_is_idempotent(tmp_path: Path) -> None:
    """INSERT OR REPLACE so adding the same IP twice doesn't create duplicates."""
    s = State(str(tmp_path / "state.db"))
    s.add_applied("1.2.3.4", "tenant")
    s.add_applied("1.2.3.4", "global")  # second add updates source
    assert s.count_applied() == 1


def test_remove_nonexistent_is_safe(tmp_path: Path) -> None:
    s = State(str(tmp_path / "state.db"))
    s.remove_applied("9.9.9.9")  # should not raise
    assert s.count_applied() == 0


def test_creates_parent_directory(tmp_path: Path) -> None:
    """State() should create the parent dir if missing."""
    nested = tmp_path / "nested" / "deeper" / "state.db"
    State(str(nested))
    assert nested.parent.is_dir()
    assert nested.exists()


def test_persistence_across_instances(tmp_path: Path) -> None:
    db = str(tmp_path / "state.db")
    s1 = State(db)
    s1.set_cursor(1000)
    s1.set_pid_snapshot(4242)
    s1.add_applied("10.0.0.1", "tenant")
    s1.add_applied("10.0.0.2", "global")

    s2 = State(db)
    assert s2.get_cursor() == 1000
    assert s2.get_pid_snapshot() == 4242
    assert s2.count_applied() == 2


def test_initial_global_cursor_is_zero(tmp_path: Path) -> None:
    s = State(str(tmp_path / "state.db"))
    assert s.get_global_cursor() == 0


def test_set_and_get_global_cursor(tmp_path: Path) -> None:
    s = State(str(tmp_path / "state.db"))
    s.set_global_cursor(2000)
    assert s.get_global_cursor() == 2000
    s.set_global_cursor(4000)
    assert s.get_global_cursor() == 4000


def test_global_cursor_persists_across_instances(tmp_path: Path) -> None:
    db = str(tmp_path / "state.db")
    s1 = State(db)
    s1.set_global_cursor(28000)
    s2 = State(db)
    assert s2.get_global_cursor() == 28000


def test_global_and_tenant_cursors_are_independent(tmp_path: Path) -> None:
    s = State(str(tmp_path / "state.db"))
    s.set_cursor(123)
    s.set_global_cursor(456)
    assert s.get_cursor() == 123
    assert s.get_global_cursor() == 456
    s.set_cursor(789)  # mover el del tenant no toca el global
    assert s.get_global_cursor() == 456
