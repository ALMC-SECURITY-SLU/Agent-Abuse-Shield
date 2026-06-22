from almc_shield.shield.health import effective_status, exit_code_for


def test_effective_status_fresh():
    assert effective_status("healthy", 10, 120) == "healthy"
    assert effective_status("degraded", 10, 120) == "degraded"


def test_effective_status_stale_is_critical():
    assert effective_status("healthy", 999, 120) == "critical"
    assert effective_status("healthy", None, 120) == "critical"


def test_effective_status_unknown_raw():
    assert effective_status(None, 10, 120) == "unknown"
    assert effective_status("weird", 10, 120) == "unknown"


def test_exit_codes():
    assert exit_code_for("healthy") == 0
    assert exit_code_for("degraded") == 1
    assert exit_code_for("critical") == 2
    assert exit_code_for("unknown") == 2
