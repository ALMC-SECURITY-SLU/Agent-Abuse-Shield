"""Configuration loader for almc-shield.

Reads /etc/almc-shield/config.ini (or a custom path) into typed dataclasses.
Fails fast if required fields are missing.
"""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ApiConfig:
    url: str
    api_key: str
    ca_bundle: Optional[str] = None
    cert_pin_sha256: Optional[str] = None
    fallback_ip: Optional[str] = None


@dataclass
class OutboxConfig:
    db_path: str = "/var/lib/almc-shield/outbox.db"
    max_size_mb: int = 100
    max_age_days: int = 7
    batch_size_max: int = 500
    batch_size_bytes_max: int = 262144


@dataclass
class SenderConfig:
    flush_interval_seconds: int = 30
    on_demand_threshold: int = 50
    timeout_connect: int = 10
    timeout_read: int = 20
    backoff_min: int = 1
    backoff_max: int = 60
    backoff_jitter: bool = True
    circuit_breaker_failures_to_open: int = 10
    circuit_breaker_cooldown_seconds: int = 60
    circuit_breaker_cooldown_cap: int = 900


@dataclass
class HeartbeatConfig:
    interval_seconds: int = 60
    interval_seconds_degraded: int = 300


@dataclass
class PullerConfig:
    interval_seconds: int = 300
    include_global: bool = True
    timeout_connect: int = 10
    timeout_read: int = 30


@dataclass
class Fail2banConfig:
    log_path: str = "/var/log/fail2ban.log"
    jail_name: str = "almc-blocklist"
    default_bantime: int = 604800


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "json"
    file: str = "/var/log/almc-shield/agent.log"
    max_size_mb: int = 50
    backup_count: int = 7


@dataclass
class RuntimeConfig:
    mode: str = "auto"     # auto | systemd | container | bare
    foreground: bool = False
    log_destination: str = "file"     # file | stdout


@dataclass
class Config:
    api: ApiConfig
    outbox: OutboxConfig = field(default_factory=OutboxConfig)
    sender: SenderConfig = field(default_factory=SenderConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    puller: PullerConfig = field(default_factory=PullerConfig)
    fail2ban: Fail2banConfig = field(default_factory=Fail2banConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)


def _get(cp: configparser.ConfigParser, section: str, key: str, default=None, cast=str):
    """Get a value from a config section with type casting."""
    if not cp.has_section(section):
        return default
    if not cp.has_option(section, key):
        return default
    raw = cp.get(section, key).strip()
    if raw == "":
        return default
    if cast is bool:
        return raw.lower() in ("1", "true", "yes", "on")
    if cast is int:
        return int(raw)
    return cast(raw)


def load(path: str) -> Config:
    """Load config from an .ini file. Raises FileNotFoundError or ValueError."""
    if not Path(path).is_file():
        raise FileNotFoundError(f"Config file not found: {path}")

    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")

    # [api] — required
    api_url = _get(cp, "api", "url", default="")
    api_key = _get(cp, "api", "api_key", default="")
    # Allow env override via ${API_KEY}
    if api_key.startswith("${") and api_key.endswith("}"):
        env_name = api_key[2:-1]
        api_key = os.environ.get(env_name, "")
    if not api_url:
        raise ValueError("config [api].url is required")
    if not api_key:
        raise ValueError("config [api].api_key is required (or set via env)")

    api = ApiConfig(
        url=api_url,
        api_key=api_key,
        ca_bundle=_get(cp, "api", "ca_bundle"),
        cert_pin_sha256=_get(cp, "api", "cert_pin_sha256"),
        fallback_ip=_get(cp, "api", "fallback_ip"),
    )

    outbox = OutboxConfig(
        db_path=_get(cp, "outbox", "db_path", OutboxConfig.db_path),
        max_size_mb=_get(cp, "outbox", "max_size_mb", OutboxConfig.max_size_mb, int),
        max_age_days=_get(cp, "outbox", "max_age_days", OutboxConfig.max_age_days, int),
        batch_size_max=_get(cp, "outbox", "batch_size_max", OutboxConfig.batch_size_max, int),
        batch_size_bytes_max=_get(cp, "outbox", "batch_size_bytes_max", OutboxConfig.batch_size_bytes_max, int),
    )

    sender = SenderConfig(
        flush_interval_seconds=_get(cp, "sender", "flush_interval_seconds", SenderConfig.flush_interval_seconds, int),
        on_demand_threshold=_get(cp, "sender", "on_demand_threshold", SenderConfig.on_demand_threshold, int),
        timeout_connect=_get(cp, "sender", "timeout_connect", SenderConfig.timeout_connect, int),
        timeout_read=_get(cp, "sender", "timeout_read", SenderConfig.timeout_read, int),
        backoff_min=_get(cp, "sender", "backoff_min", SenderConfig.backoff_min, int),
        backoff_max=_get(cp, "sender", "backoff_max", SenderConfig.backoff_max, int),
        backoff_jitter=_get(cp, "sender", "backoff_jitter", SenderConfig.backoff_jitter, bool),
        circuit_breaker_failures_to_open=_get(cp, "sender", "circuit_breaker_failures_to_open", SenderConfig.circuit_breaker_failures_to_open, int),
        circuit_breaker_cooldown_seconds=_get(cp, "sender", "circuit_breaker_cooldown_seconds", SenderConfig.circuit_breaker_cooldown_seconds, int),
        circuit_breaker_cooldown_cap=_get(cp, "sender", "circuit_breaker_cooldown_cap", SenderConfig.circuit_breaker_cooldown_cap, int),
    )

    heartbeat = HeartbeatConfig(
        interval_seconds=_get(cp, "heartbeat", "interval_seconds", HeartbeatConfig.interval_seconds, int),
        interval_seconds_degraded=_get(cp, "heartbeat", "interval_seconds_degraded", HeartbeatConfig.interval_seconds_degraded, int),
    )

    puller = PullerConfig(
        interval_seconds=_get(cp, "puller", "interval_seconds", PullerConfig.interval_seconds, int),
        include_global=_get(cp, "puller", "include_global", PullerConfig.include_global, bool),
        timeout_connect=_get(cp, "puller", "timeout_connect", PullerConfig.timeout_connect, int),
        timeout_read=_get(cp, "puller", "timeout_read", PullerConfig.timeout_read, int),
    )

    fail2ban = Fail2banConfig(
        log_path=_get(cp, "fail2ban", "log_path", Fail2banConfig.log_path),
        jail_name=_get(cp, "fail2ban", "jail_name", Fail2banConfig.jail_name),
        default_bantime=_get(cp, "fail2ban", "default_bantime", Fail2banConfig.default_bantime, int),
    )

    logging = LoggingConfig(
        level=_get(cp, "logging", "level", LoggingConfig.level),
        format=_get(cp, "logging", "format", LoggingConfig.format),
        file=_get(cp, "logging", "file", LoggingConfig.file),
        max_size_mb=_get(cp, "logging", "max_size_mb", LoggingConfig.max_size_mb, int),
        backup_count=_get(cp, "logging", "backup_count", LoggingConfig.backup_count, int),
    )

    runtime = RuntimeConfig(
        mode=_get(cp, "runtime", "mode", RuntimeConfig.mode),
        foreground=_get(cp, "runtime", "foreground", RuntimeConfig.foreground, bool),
        log_destination=_get(cp, "runtime", "log_destination", RuntimeConfig.log_destination),
    )

    return Config(
        api=api, outbox=outbox, sender=sender, heartbeat=heartbeat, puller=puller,
        fail2ban=fail2ban, logging=logging, runtime=runtime,
    )
