<p align="center">
  <img src="docs/assets/logo.png" alt="ALMC SHIELD — Abuse Shield, Web & Defense Infrastructure" width="320">
</p>

<h1 align="center">ALMC SHIELD agent</h1>

<p align="center">
  <strong>Open-source fail2ban → cloud aggregator.</strong><br>
  Block the attacker once, protect every server in your fleet.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.8%2B-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/status-production-green.svg" alt="Status">
  <a href="https://almc.es/shield"><img src="https://img.shields.io/badge/marketing-almc.es%2Fshield-0099cc.svg" alt="Marketing"></a>
</p>


ALMC SHIELD is the lightweight Linux agent that powers the [Abuse Shield service](https://almc.es/shield). It reads your local `fail2ban.log`, ships each ban event to a multi-tenant central API, and pulls back the de-duplicated, enrichment-validated blocklist so every server in your fleet bans the same attacker — within seconds.

## How it works

```
fail2ban.log ──▶ reader ──▶ batch sender ──HTTPS──▶ almc.es API
                                                       │
fail2ban-client ◀── puller ◀──────────HTTPS─────────── │
                                                       ▼
                                       enrichment (AbuseIPDB, IPInfo, WHOIS)
                                                       │
                                                       ▼
                                       per-tenant blocklist + global feed
```

## Features

- **Real-time**: each ban is shipped within 30s (batched).
- **Bidirectional**: receive the global tenant blocklist within 5 min.
- **Multi-tenant**: a single API key per tenant covers any number of servers.
- **Datacenter-aware**: IPs from cloud providers (AWS, Azure, OVH, etc.) are auto-flagged and skip the global feed (false-positive shield).
- **Operationally boring**: systemd unit, structured logs (`/var/log/almc-shield/agent.log`), self-resuming on restart (state persisted in `/var/lib/almc-shield/state.db`).
- **Sane footprint**: ~30 MB RAM, ~0% CPU at idle, <128 MB hard cap.

## Quick install

```bash
curl -fsSL https://almc.es/abuse-shield/install.sh | sudo bash -s -- --api-key=ab_live_XXX
```

Or with environment variable:
```bash
curl -fsSL https://almc.es/abuse-shield/install.sh | sudo ABUSE_SHIELD_API_KEY=ab_live_XXX bash
```

Get your API key at https://almc.es/es/dash/abuse-shield/settings (free tier available).

## Supported platforms

| Distro family | Tested versions | Package manager |
|---------------|-----------------|-----------------|
| Debian / Ubuntu | 11+, 20.04+ | `apt-get` |
| RHEL / Rocky / AlmaLinux | 8+, 9+ | `dnf` / `yum` |
| Alpine | 3.16+ | `apk` |

Python 3.8+ required. The installer detects what's missing and pulls it from the system repos.

## Manual install (from source)

```bash
git clone https://github.com/ALMC-SECURITY-SLU/Agent-Abuse-Shield.git
cd Agent-Abuse-Shield
sudo ./install.sh --api-key=ab_live_XXX
```

## Uninstall

```bash
curl -fsSL https://almc.es/abuse-shield/uninstall.sh | sudo bash
```

Stops the service, removes `/opt/almc-shield`, `/etc/almc-shield`, `/var/lib/almc-shield`, `/var/log/almc-shield`, the systemd unit, the fail2ban jail and the sudoers entry. Keeps your fail2ban config untouched.

## Configuration

Main config at `/etc/almc-shield/config.ini`. Defaults work out-of-the-box; tweak only what you need:

```ini
[api]
url = https://almc.es/api/v1/abuse
key = ab_live_XXXXX

[reader]
fail2ban_log = /var/log/fail2ban.log

[sender]
batch_size = 50
batch_interval_seconds = 30

[puller]
interval_seconds = 300
```

See [`config.ini.example`](config.ini.example) for the full list.

## Architecture

The agent is built as 4 cooperating threads:

| Thread | Job |
|--------|-----|
| **reader** | Tails `fail2ban.log` via `watchdog`, enqueues new `Ban` events. |
| **sender** | Drains the queue every 30s, POSTs batches to `/report`, retries with backoff on 5xx, persists outbox in `state.db` for restart-safety. |
| **puller** | Every 5 min, hits `/blocklist?since=<cursor>` for delta; on cold boot pulls `/blocklist/full`. Applies via `fail2ban-client banip`. |
| **heartbeat** | Every 60s, POSTs runtime stats (queue size, last error, version) to `/heartbeat`. |

All threads share a single `state.db` (SQLite, WAL mode). Crash-safe.

## Development

```bash
git clone https://github.com/ALMC-SECURITY-SLU/Agent-Abuse-Shield.git
cd Agent-Abuse-Shield
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-mock
pytest -v
```

See [CONTRIBUTING.md](#contributing) for guidelines.

## Security

- The agent runs as `almc-shield` (non-root system user).
- `sudoers.d/almc-shield` restricts sudo to **4 explicit subcommands** of `fail2ban-client` — nothing else.
- systemd unit hardening: `ProtectSystem=strict`, `PrivateTmp`, `ProtectHome`, `MemoryMax=128M`.
- API key stored chmod `600`, owned by `almc-shield:almc-shield`.
- All traffic over HTTPS with the central API.
- Found a vulnerability? See [SECURITY.md](#security-disclosures).

## Roadmap

- [ ] Plesk / cPanel / DirectAdmin / aaPanel auto-detection
- [ ] SELinux context auto-configuration
- [ ] `journald`-only environments (no `fail2ban.log` file)
- [ ] CrowdSec bouncer interop
- [ ] Prometheus `/metrics` endpoint
- [ ] Multi-jail support (whitelist via config)

## Contributing

PRs welcome. Please:
1. Run `pytest -v` and `ruff check .` before opening.
2. Update `CHANGELOG.md` under `[Unreleased]`.
3. Squash to a single conventional commit per PR.

## License

Apache License 2.0 — see [LICENSE](LICENSE).

## Links

- **Marketing site**: https://almc.es/shield
- **Dashboard**: https://almc.es/es/dash/abuse-shield
- **API docs**: https://almc.es/es/dash/abuse-shield/docs
- **Support**: soporte@almc.es

---

Built by [ALMC Security SLU](https://almc.es) · Made in Spain
