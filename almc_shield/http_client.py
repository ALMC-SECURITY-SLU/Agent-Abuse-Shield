"""Lightweight wrapper around httpx for sender + future puller/heartbeat.

Centralises auth header, TLS config, timeouts, user-agent.
"""
from __future__ import annotations

from typing import Optional

import httpx

from almc_shield.config import Config
from almc_shield.version import __version__


def build_client(cfg: Config) -> httpx.Client:
    """Build a configured httpx.Client. Caller is responsible for closing."""
    headers = {
        "Authorization": f"Bearer {cfg.api.api_key}",
        "User-Agent": f"almc-shield/{__version__}",
        "Accept": "application/json",
    }
    verify: Optional[str] = cfg.api.ca_bundle if cfg.api.ca_bundle else True
    timeout = httpx.Timeout(
        connect=cfg.sender.timeout_connect,
        read=cfg.sender.timeout_read,
        write=cfg.sender.timeout_connect,
        pool=cfg.sender.timeout_connect,
    )
    return httpx.Client(
        base_url=cfg.api.url.rstrip("/"),
        headers=headers,
        verify=verify,
        timeout=timeout,
        follow_redirects=False,
    )
