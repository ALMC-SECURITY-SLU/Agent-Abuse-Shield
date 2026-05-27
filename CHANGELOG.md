# Changelog

All notable changes to the ALMC SHIELD agent will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.1] - 2026-05-28

### Added
- Brand logo (`docs/assets/logo.png`) embedded at the top of the README.

## [1.0.0] - 2026-05-28

### Added
- Initial public release.
- Bidirectional sync: agent reports bans + pulls global blocklist back.
- Multi-tenant via API key (`ab_live_*` / `ab_test_*`).
- fail2ban log reader with watchdog (real-time).
- Sender with batching + outbox + circuit breaker.
- Puller with delta cursor + full-sync on cold boot.
- Heartbeat with runtime telemetry.
- install.sh: auto-detect Debian/RHEL/Alpine, auto-install fail2ban + python3-venv, fix `/var/log/fail2ban.log` perms, install systemd unit with hardening, restricted sudoers (4 fail2ban-client subcommands).
- uninstall.sh: clean removal preserving fail2ban config.
- Install telemetry: each step reports to `/api/v1/abuse/install-event` so the customer's dashboard shows live progress.

[Unreleased]: https://github.com/ALMC-SECURITY-SLU/Agent-Abuse-Shield/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/ALMC-SECURITY-SLU/Agent-Abuse-Shield/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/ALMC-SECURITY-SLU/Agent-Abuse-Shield/releases/tag/v1.0.0
