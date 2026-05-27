"""Wrapper around `fail2ban-client` for banip/unbanip/status operations.
Uses sudo (sudoers limits exactly these 4 commands)."""
from __future__ import annotations
import subprocess
import structlog

log = structlog.get_logger(__name__)


class F2bClient:
    def __init__(self, jail: str = "almc-blocklist", binary: str = "/usr/bin/fail2ban-client",
                 sudo: bool = True, timeout: int = 2):
        self.jail = jail
        self.binary = binary
        self.sudo = sudo
        self.timeout = timeout

    def _run(self, *args: str) -> tuple[int, str, str]:
        cmd = (["sudo", "-n", self.binary] if self.sudo else [self.binary]) + list(args)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
            return r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            log.warning("f2b_timeout", cmd=cmd)
            return 124, "", "timeout"
        except FileNotFoundError:
            log.warning("f2b_binary_missing", binary=self.binary)
            return 127, "", "binary not found"

    def banip(self, ip: str) -> bool:
        rc, _, err = self._run("set", self.jail, "banip", ip)
        if rc != 0:
            log.warning("f2b_banip_failed", ip=ip, rc=rc, err=err[:200])
            return False
        return True

    def unbanip(self, ip: str) -> bool:
        rc, _, err = self._run("set", self.jail, "unbanip", ip)
        if rc != 0:
            log.warning("f2b_unbanip_failed", ip=ip, rc=rc, err=err[:200])
            return False
        return True

    def status_count(self) -> int | None:
        """Parse `Currently banned:` count from `fail2ban-client status <jail>` output."""
        rc, out, _ = self._run("status", self.jail)
        if rc != 0:
            return None
        for line in out.splitlines():
            if "Currently banned" in line:
                try:
                    return int(line.split(":")[-1].strip())
                except ValueError:
                    return None
        return None

    def server_pid(self) -> int | None:
        try:
            with open("/var/run/fail2ban/fail2ban.pid", "r", encoding="ascii") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None
