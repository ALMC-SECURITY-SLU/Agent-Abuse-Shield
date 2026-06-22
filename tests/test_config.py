"""Tests for config.py."""
from pathlib import Path

import pytest

from almc_shield import config


def test_loads_minimal_config(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.ini"
    cfg_file.write_text(
        """
[api]
url = https://almc.es/api/v1/abuse
api_key = ab_live_test123

[fail2ban]
log_path = /var/log/fail2ban.log
"""
    )
    c = config.load(str(cfg_file))
    assert c.api.url == "https://almc.es/api/v1/abuse"
    assert c.api.api_key == "ab_live_test123"
    assert c.fail2ban.log_path == "/var/log/fail2ban.log"


def test_defaults_applied_when_missing(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.ini"
    cfg_file.write_text(
        """
[api]
url = https://almc.es/api/v1/abuse
api_key = X
"""
    )
    c = config.load(str(cfg_file))
    # Defaults
    assert c.outbox.max_size_mb == 100
    assert c.outbox.batch_size_max == 500
    assert c.sender.flush_interval_seconds == 30
    assert c.sender.timeout_connect == 10
    assert c.sender.backoff_min == 1
    assert c.sender.backoff_max == 60
    assert c.heartbeat.interval_seconds == 60
    assert c.fail2ban.jail_name == "almc-blocklist"
    assert c.logging.level == "INFO"


def test_raises_when_api_key_missing(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.ini"
    cfg_file.write_text(
        """
[api]
url = https://almc.es/api/v1/abuse
"""
    )
    with pytest.raises(ValueError, match="api_key"):
        config.load(str(cfg_file))


def test_raises_when_file_missing() -> None:
    with pytest.raises(FileNotFoundError):
        config.load("/nonexistent/config.ini")


def test_shield_defaults(tmp_path):
    p = tmp_path / "c.ini"
    p.write_text("[api]\nurl = https://almc.es/api/v1/abuse\napi_key = ab_live_x\n")
    from almc_shield.config import load
    cfg = load(str(p))
    assert cfg.shield.interval_seconds == 2
    assert cfg.shield.rows == 10
    assert cfg.shield.color == "auto"
    assert cfg.shield.panels == "feed,queue,threads,bans"


def test_shield_overrides(tmp_path):
    p = tmp_path / "c.ini"
    p.write_text(
        "[api]\nurl = https://almc.es/api/v1/abuse\napi_key = ab_live_x\n"
        "[shield]\ninterval_seconds = 5\nrows = 20\ncolor = never\npanels = feed,bans\n"
    )
    from almc_shield.config import load
    cfg = load(str(p))
    assert cfg.shield.interval_seconds == 5
    assert cfg.shield.rows == 20
    assert cfg.shield.color == "never"
    assert cfg.shield.panels == "feed,bans"
