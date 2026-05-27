"""fail2ban.log line parser -> BanEvent.

The fail2ban default log format (v0.10+) is:
  YYYY-MM-DD HH:MM:SS,mmm fail2ban.actions [PID]: NOTICE  [<jail>] Ban <ip>

Both IPv4 and IPv6 (any compressed form) are supported.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


# IPv4: 4 octets 0-255 separated by dots
IPV4_OCTET = r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)"
IPV4_RE = rf"(?:{IPV4_OCTET}\.){{3}}{IPV4_OCTET}"

# IPv6: 1-7 groups of 1-4 hex chars separated by colons, ending in hex or :
# Accepts compressed (::) and full forms; uses a permissive matcher then validates with ipaddress
# below if needed. For perf we keep regex broad and trust fail2ban to write valid IPs.
#
# Order matters: try forms with embedded "::" BEFORE the strict 7-colon form, so
# the alternation backtracks correctly. We list distinct sub-patterns that cover
# the most common compressed shapes.
IPV6_RE = (
    # Full unabbreviated (8 groups)
    r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}"
    # Leading "::" (e.g. ::1, ::ffff:1.2.3.4 — last form not strictly needed)
    r"|::(?:[0-9a-fA-F]{1,4}:){0,6}[0-9a-fA-F]{1,4}"
    # Trailing "::" (e.g. fe80::)
    r"|(?:[0-9a-fA-F]{1,4}:){1,7}:"
    # Embedded "::" with hex groups on both sides (e.g. fe80::1, 2001:db8::1, 2606:4700:4700::1111)
    r"|(?:[0-9a-fA-F]{1,4}:){1,6}(?::[0-9a-fA-F]{1,4}){1,6}"
    # Just "::"
    r"|::"
)

BAN_LINE_RE = re.compile(
    rf"^(?P<ts>\d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}:\d{{2}})(?:,\d+)?\s+"
    rf"fail2ban\.\S+\s+\[\d+\]:\s+\S+\s+"
    rf"\[(?P<jail>[a-zA-Z0-9_-]+)\]\s+Ban\s+"
    rf"(?P<ip>{IPV4_RE}|{IPV6_RE})\s*$"
)


@dataclass(frozen=True)
class BanEvent:
    """A parsed Ban event from fail2ban.log.

    `banned_at` is stored as a TIMEZONE-AWARE datetime in UTC. The parser
    interprets timestamps from fail2ban (which writes them in the system's
    local timezone) and converts them to UTC, so to_dict() always emits a
    correct ISO 8601 UTC string that the server's `before:+5min` validation
    accepts.
    """
    jail: str
    ip: str
    banned_at: datetime  # always UTC-aware

    def to_dict(self) -> dict:
        # Always serialize as UTC ISO 8601 with trailing Z.
        utc = self.banned_at.astimezone(timezone.utc) if self.banned_at.tzinfo else self.banned_at.replace(tzinfo=timezone.utc)
        return {
            "jail": self.jail,
            "ip": self.ip,
            "banned_at": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


def parse_ban_line(line: str) -> Optional[BanEvent]:
    """Parse one log line. Return BanEvent or None if not a Ban event.

    fail2ban writes timestamps in the system's LOCAL timezone (per its
    default Logging settings). We interpret the naive timestamp as local
    and convert to UTC for downstream consumption.
    """
    m = BAN_LINE_RE.match(line.rstrip("\n").rstrip("\r"))
    if not m:
        return None
    try:
        naive = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    # Interpret naive timestamp as local time, convert to UTC-aware
    local_aware = naive.astimezone()  # attaches system local tzinfo
    utc_aware = local_aware.astimezone(timezone.utc)
    return BanEvent(jail=m.group("jail"), ip=m.group("ip"), banned_at=utc_aware)
