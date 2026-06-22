# Changelog

All notable changes to the ALMC SHIELD agent will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.6] - 2026-06-22

### Added
- **Comando `shield`** (panel del agente en el servidor, sin abrir puertos):
  - `shield` → TUI interactiva en vivo (estilo htop); `shield status` / `--once` /
    `--json` / `--check` (exit 0/1/2 por salud, para monitorización).
  - Acciones: `shield update`, `disable`/`enable`, `feed-global off`/`on`,
    `uninstall` — requieren root, piden confirmación (salvo `--yes`) y reportan al
    backend best-effort. `feed-global off` desbloquea las IPs del feed global con aviso.
  - Sección `[shield]` en config.ini (interval, rows, color, panels).
- El agente escribe un snapshot de estado periódico (para `shield`) y persiste las
  stats del feed; `puller` respeta `[puller] include_global` de la config.

### Fixed
- `sender.py` reportaba `agent_version` hardcodeado "1.0.0"; ahora usa la versión real
  (causa de que el panel mostrara 1.0.0).

## [1.0.5] - 2026-06-22

### Fixed
- **Paginación del feed global**: el agente solo aplicaba ~2.000 de las ~28.000
  IPs del feed colectivo. `puller.pull_once` ahora envía `global_since` con un
  cursor global separado (`state.global_cursor`) y drena el feed en bucle hasta
  vaciarlo en un ciclo; `_full_sync` itera todas las páginas de `/blocklist/full`
  en vez de pedir solo `page=1`. Backend sin cambios.

## [1.0.4] - 2026-05-28

### Added — installer hardening (6 detecciones extra)
- **Disk space check** pre-flight: requiere ≥200MB libres en `/opt` y `/var`
  antes de tocar nada. Fatal con mensaje claro si insuficiente.
- **DNS + proxy + TLS handshake check** pre-flight: verifica resolución DNS
  de `almc.es`, detecta `HTTP_PROXY`/`HTTPS_PROXY` en env vars y
  `/etc/environment`, prueba TLS handshake real (no solo TCP).
- **Docker/Podman/OpenVZ/nspawn detection**: extiende el LXC namespacing-
  disable a más container types. Sin esto el agente NUNCA arrancaba en
  containers Docker no-privileged.
- **EPEL auto-install antes de fail2ban en RHEL**: `rpm -q epel-release` +
  `dnf install epel-release` automático (fail2ban vive en EPEL, no en
  core repos de RHEL/Rocky/Alma).
- **`policycoreutils-python-utils` auto en RHEL + SELinux**: si SELinux
  Enforcing pero faltan `semanage`/`restorecon`, los instala primero. En
  RHEL minimal cloud images no vienen por default.
- **Sudoers `includedir` verify**: comprueba que `/etc/sudoers` carga
  `/etc/sudoers.d/`. Si no, warn + nota para añadir
  `@includedir /etc/sudoers.d` manualmente.

### Added — operational
- **Trap EXIT con cleanup parcial**: si el install se aborta a mitad
  (Ctrl+C, error inesperado), detiene el servicio si arrancó y reporta
  `install_exit_X` al backend (no borra archivos para que el admin
  pueda inspeccionar antes de `--reinstall`).

### Fixed
- `grep ... /etc/environment | head -1 | cut ...` sin `|| true` mataba el
  script con `set -euo pipefail` si el grep no matcheaba (caso típico:
  server sin proxy configurado → grep exit 1 → pipefail propaga → trap
  capturaba como abort falso).

## [1.0.3] - 2026-05-28

### Added — installer (12 mejoras)
- **Panel detection**: auto-detecta Plesk, cPanel, DirectAdmin, aaPanel, ISPConfig
  y emite warn + telemetría con nota de coexistencia específica por panel.
- **fail2ban `logtarget` check**: fatal si está en `SYSTEMD-JOURNAL` o `SYSLOG`
  (el agente lee archivo, no journal). Antes el agente arrancaba OK pero zero
  reports forever — fallo silencioso devastador.
- **SELinux auto-configure**: si Enforcing → `semanage fcontext var_log_t` +
  `restorecon` + `setsebool daemons_use_tty`. Skip con `--skip-selinux`.
- **`/etc/os-release` PRETTY_NAME**: ahora aparece en banner + telemetría
  install-event (antes solo `uname -s -r` genérico). El panel del cliente
  muestra "Ubuntu 24.04 LTS" en vez de "Linux 6.8.0".
- **fail2ban version min check**: warn no-fatal si <0.10.
- **`--dry-run`**: ejecuta TODAS las checks sin modificar nada. Ideal para
  auditoría pre-compra en compliance.
- **Health check E2E post-install**: verifica systemctl active + log producido
  + POST `/heartbeat` responde 200. Detecta install OK pero servicio roto.
- **Verificación SHA-256 del tarball**: descarga sidecar
  `abuse-shield-agent-X.Y.Z.tar.gz.sha256`, valida con `sha256sum -c`. Fatal
  si mismatch; no bloqueante si el sidecar no existe.
- **`--tarball=PATH`**: usa tarball local (airgapped / hospitales / banca).
- **`--auto-update`**: instala systemd timer semanal (`Sun 03:00 ± 2h`) que
  checkea GitHub `/releases/latest` y se auto-reinstala con `--reinstall`.
- **Soporte SUSE / openSUSE** (zypper) además de apt/dnf/yum/apk.
- **`--reinstall`**: descarga + ejecuta `uninstall.sh`, luego reinstala. One-shot
  upgrade desde el campo.

### Added — installer (extra)
- **Auto-detección LXC** con `systemd-detect-virt` + fallback `/proc/1/cgroup`.
  Si LXC sin privilegios: comenta `ProtectSystem` / `ProtectHome` / `PrivateTmp` /
  `ReadWritePaths` del systemd unit (mount namespacing requiere `CAP_SYS_ADMIN`
  que no hay en LXC unprivileged → exit code 226/NAMESPACE). Sin esto el
  agente NUNCA arrancaba en containers LXC.

### Fixed — agent (críticos)
- **Reader cold-boot OOM**: `_load_offset()` ahora arranca en EOF del log si
  no hay state file previo (cold boot en host nuevo). En hosts con
  `/var/log/fail2ban.log` >100MB acumulados, el peak RSS pasaba de ~900MB →
  OOM kill del agente. Con tail-mode el peak es ~50MB en estado estable.
- **`_stop` attribute collision**: `heartbeat.py`, `puller.py` y `main.py`
  sobreescribían `self._stop = threading.Event()`, ocultando el método
  `_stop()` heredado de `threading.Thread`. Eso rompía `.join()` con
  `TypeError: 'Event' object is not callable` en shutdown. Renombrado a
  `self._stop_event` en los 3 archivos.

### Changed — systemd unit
- `MemoryMax` 128M → 768M (cubre peak inicial sin OOM; estable ~30M).
- `StartLimitBurst` / `StartLimitIntervalSec` movidos a `[Unit]`
  (deprecated en `[Service]` desde systemd 230 — generaba warning).

### Changed — uninstall.sh
- Para los timer `almc-shield-update.{service,timer}` si existen
  (antes los dejaba huérfanos tras un install con `--auto-update`).
- Hace `fail2ban-client unban --all` antes de borrar el jail.
- `fail2ban-client reload` tras borrar `.conf` para que el jail desaparezca
  de memoria de fail2ban.

## [1.0.2] - 2026-05-28

### Changed
- Cleaned up internal development-phase references (F3/F4/F11) from code and
  config comments. No behaviour change — only clearer, self-contained
  documentation for public readers.

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

[Unreleased]: https://github.com/ALMC-SECURITY-SLU/Agent-Abuse-Shield/compare/v1.0.4...HEAD
[1.0.4]: https://github.com/ALMC-SECURITY-SLU/Agent-Abuse-Shield/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/ALMC-SECURITY-SLU/Agent-Abuse-Shield/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/ALMC-SECURITY-SLU/Agent-Abuse-Shield/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/ALMC-SECURITY-SLU/Agent-Abuse-Shield/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/ALMC-SECURITY-SLU/Agent-Abuse-Shield/releases/tag/v1.0.0
