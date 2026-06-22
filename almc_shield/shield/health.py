"""Deriva el estado efectivo (considerando frescura del snapshot) y el exit code."""
from __future__ import annotations


def effective_status(raw_status, snapshot_age_seconds, max_age_seconds) -> str:
    # Sin snapshot o snapshot viejo => el agente no se está actualizando => crítico.
    if snapshot_age_seconds is None or snapshot_age_seconds > max_age_seconds:
        return "critical"
    if raw_status in ("healthy", "degraded", "critical"):
        return raw_status
    return "unknown"


def exit_code_for(status: str) -> int:
    return {"healthy": 0, "degraded": 1}.get(status, 2)
