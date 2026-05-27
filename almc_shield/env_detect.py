"""Detect runtime environment: systemd | container | podman | bare."""
from __future__ import annotations

import os
from pathlib import Path


def detect_environment() -> str:
    """Return one of: systemd, container, podman, bare."""
    if Path("/.dockerenv").exists():
        return "container"
    try:
        cgroup = Path("/proc/1/cgroup").read_text()
        if "docker" in cgroup or "containerd" in cgroup or "kubepods" in cgroup:
            return "container"
    except OSError:
        pass
    if Path("/run/.containerenv").exists():
        return "podman"
    if Path("/run/systemd/system").exists():
        return "systemd"
    return "bare"
