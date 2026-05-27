"""Tests for f2b_client.py — mocks subprocess.run."""
from unittest.mock import MagicMock, patch

import pytest

from almc_shield.f2b_client import F2bClient


def _completed(rc: int, out: str = "", err: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = rc
    m.stdout = out
    m.stderr = err
    return m


def test_banip_success() -> None:
    with patch("subprocess.run", return_value=_completed(0)) as run:
        c = F2bClient(jail="almc-blocklist", sudo=False)
        assert c.banip("1.2.3.4") is True
    # verify the command shape
    call_args = run.call_args.args[0]
    assert call_args == ["/usr/bin/fail2ban-client", "set", "almc-blocklist", "banip", "1.2.3.4"]


def test_banip_failure_returns_false() -> None:
    with patch("subprocess.run", return_value=_completed(1, err="ip already banned")):
        c = F2bClient(jail="almc-blocklist", sudo=False)
        assert c.banip("1.2.3.4") is False


def test_unbanip_success() -> None:
    with patch("subprocess.run", return_value=_completed(0)) as run:
        c = F2bClient(jail="almc-blocklist", sudo=False)
        assert c.unbanip("1.2.3.4") is True
    call_args = run.call_args.args[0]
    assert call_args == ["/usr/bin/fail2ban-client", "set", "almc-blocklist", "unbanip", "1.2.3.4"]


def test_unbanip_failure_returns_false() -> None:
    with patch("subprocess.run", return_value=_completed(1)):
        c = F2bClient(jail="almc-blocklist", sudo=False)
        assert c.unbanip("1.2.3.4") is False


def test_sudo_prepends_when_enabled() -> None:
    """Default sudo=True should prepend sudo -n to the command."""
    with patch("subprocess.run", return_value=_completed(0)) as run:
        c = F2bClient(jail="almc-blocklist", sudo=True)
        c.banip("1.2.3.4")
    call_args = run.call_args.args[0]
    assert call_args[:2] == ["sudo", "-n"]
    assert call_args[2] == "/usr/bin/fail2ban-client"


def test_status_count_parses_currently_banned() -> None:
    sample = """Status for the jail: almc-blocklist
|- Filter
|  `- Currently failed: 0
`- Actions
   |- Currently banned: 42
   `- Total banned: 100"""
    with patch("subprocess.run", return_value=_completed(0, out=sample)):
        c = F2bClient(jail="almc-blocklist", sudo=False)
        assert c.status_count() == 42


def test_status_count_returns_none_when_unparseable() -> None:
    with patch("subprocess.run", return_value=_completed(0, out="garbage output no banned line")):
        c = F2bClient(jail="almc-blocklist", sudo=False)
        assert c.status_count() is None


def test_status_count_returns_none_when_value_not_integer() -> None:
    with patch("subprocess.run", return_value=_completed(0, out="Currently banned: garbage")):
        c = F2bClient(jail="almc-blocklist", sudo=False)
        assert c.status_count() is None


def test_status_count_returns_none_when_rc_nonzero() -> None:
    with patch("subprocess.run", return_value=_completed(1, out="")):
        c = F2bClient(jail="almc-blocklist", sudo=False)
        assert c.status_count() is None


def test_timeout_returns_124() -> None:
    """subprocess.TimeoutExpired returns rc=124 and a False banip."""
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("fail2ban-client", 2)):
        c = F2bClient(jail="almc-blocklist", sudo=False)
        assert c.banip("1.2.3.4") is False


def test_binary_missing_returns_127() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        c = F2bClient(jail="almc-blocklist", sudo=False)
        assert c.banip("1.2.3.4") is False


def test_server_pid_reads_pidfile() -> None:
    """Use unittest.mock.mock_open so server_pid() reads our canned content."""
    from unittest.mock import mock_open
    c = F2bClient(jail="almc-blocklist", sudo=False)
    m = mock_open(read_data="12345\n")
    with patch("builtins.open", m):
        assert c.server_pid() == 12345


def test_server_pid_returns_none_when_missing() -> None:
    c = F2bClient(jail="almc-blocklist", sudo=False)
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert c.server_pid() is None


def test_server_pid_returns_none_when_invalid() -> None:
    from unittest.mock import mock_open
    c = F2bClient(jail="almc-blocklist", sudo=False)
    m = mock_open(read_data="not-a-number\n")
    with patch("builtins.open", m):
        assert c.server_pid() is None
