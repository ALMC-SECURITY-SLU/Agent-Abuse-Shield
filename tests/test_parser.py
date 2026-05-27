"""Tests for parser.py.

NOTE on timezones: the parser intentionally interprets fail2ban's naive
local timestamps and converts to UTC-aware (see parser.parse_ban_line
docstring). The test fixtures supply UTC-aware datetimes because that's
the contract of BanEvent.banned_at after the fix. We assert the LOCAL
hour matches by reconverting — this keeps the test stable on any host
timezone (CI / dev / prod).
"""
from datetime import datetime, timezone
import pytest

from almc_shield.parser import BanEvent, parse_ban_line


# Real fail2ban log line samples (v0.10+ default format). The "expected hour"
# is the LOCAL hour as written in the log; the parser must convert to UTC.
FAIL2BAN_LINES = {
    # IPv4
    "2026-05-13 10:23:45,123 fail2ban.actions [12345]: NOTICE  [sshd] Ban 1.2.3.4":
        ("sshd", "1.2.3.4", datetime(2026, 5, 13, 10, 23, 45)),
    "2026-05-13 11:00:00,000 fail2ban.actions [12345]: NOTICE  [wp-login] Ban 5.6.7.8":
        ("wp-login", "5.6.7.8", datetime(2026, 5, 13, 11, 0, 0)),
    # IPv6
    "2026-05-13 10:23:45,123 fail2ban.actions [12345]: NOTICE  [sshd] Ban 2001:db8::1":
        ("sshd", "2001:db8::1", datetime(2026, 5, 13, 10, 23, 45)),
    "2026-05-13 10:23:45,123 fail2ban.actions [12345]: NOTICE  [sshd] Ban 2606:4700:4700::1111":
        ("sshd", "2606:4700:4700::1111", datetime(2026, 5, 13, 10, 23, 45)),
    # IPv6 compressed
    "2026-05-13 10:23:45,123 fail2ban.actions [12345]: NOTICE  [sshd] Ban fe80::1":
        ("sshd", "fe80::1", datetime(2026, 5, 13, 10, 23, 45)),
}


@pytest.mark.parametrize("line,expected", FAIL2BAN_LINES.items())
def test_parses_ban_line(line: str, expected: tuple) -> None:
    event = parse_ban_line(line)
    assert event is not None, f"failed to parse: {line!r}"
    assert event.jail == expected[0]
    assert event.ip == expected[1]
    # banned_at must be tz-aware UTC. Re-convert to local naive to compare with
    # the local hour the fixture asserts (test stable on any host timezone).
    assert event.banned_at.tzinfo is not None
    local_naive = event.banned_at.astimezone().replace(tzinfo=None)
    assert local_naive == expected[2]


def test_returns_none_for_non_ban_lines() -> None:
    assert parse_ban_line("") is None
    assert parse_ban_line("2026-05-13 10:00:00,000 fail2ban.server [123]: INFO  Started") is None
    assert parse_ban_line("2026-05-13 10:00:00,000 fail2ban.actions [123]: NOTICE  [sshd] Unban 1.2.3.4") is None
    assert parse_ban_line("random text") is None


def test_ban_event_to_dict_emits_utc() -> None:
    """Construct with explicit UTC-aware → to_dict emits the UTC string."""
    e = BanEvent(jail="sshd", ip="1.2.3.4",
                 banned_at=datetime(2026, 5, 13, 10, 0, 0, tzinfo=timezone.utc))
    assert e.to_dict() == {"jail": "sshd", "ip": "1.2.3.4", "banned_at": "2026-05-13T10:00:00Z"}


def test_ban_event_to_dict_treats_naive_as_utc() -> None:
    """If somehow constructed naive (legacy), to_dict treats it as UTC."""
    e = BanEvent(jail="sshd", ip="1.2.3.4",
                 banned_at=datetime(2026, 5, 13, 10, 0, 0))  # no tzinfo
    assert e.to_dict() == {"jail": "sshd", "ip": "1.2.3.4", "banned_at": "2026-05-13T10:00:00Z"}
