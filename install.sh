#!/bin/bash
# install.sh — Install ALMC Abuse Shield agent (v1.0.5).
#
# Usage (running as root, e.g. inside an LXC container or after `su -`):
#   ./install.sh --api-key=ab_live_XXXXX
#   curl -fsSL https://almc.es/abuse-shield/install.sh | bash -s -- --api-key=ab_live_XXX
#   curl -fsSL https://almc.es/abuse-shield/install.sh | ABUSE_SHIELD_API_KEY=ab_live_XXX bash
#
# Usage (regular user with sudo on host):
#   curl -fsSL https://almc.es/abuse-shield/install.sh | sudo bash -s -- --api-key=ab_live_XXX
#   curl -fsSL https://almc.es/abuse-shield/install.sh | sudo -E ABUSE_SHIELD_API_KEY=ab_live_XXX bash
#
# Flags:
#   --api-key=KEY              tu API key (formato ab_live_xxx | ab_test_xxx)
#   --api-url=URL              override del endpoint API
#   --dry-run                  ejecuta solo checks, no modifica el sistema
#   --reinstall                desinstala primero, luego instala (one-shot upgrade)
#   --tarball=PATH             usa tarball local en vez de descargar (airgapped)
#   --auto-update              instala timer systemd que checkea releases en GitHub
#   --skip-fail2ban-install    no instala fail2ban automáticamente si falta
#   --skip-api-test            saltar el test de conectividad con el API
#   --skip-jail-reload         no recargar fail2ban tras copiar el jail
#   --force-permissions        forzar fix de permisos en /var/log/fail2ban.log
#   --skip-health-check        no ejecutar el health check E2E al final
#   --skip-selinux             no auto-configurar el contexto SELinux
#   --no-color                 desactivar colores ANSI
#   -h | --help                muestra esta ayuda
#
# Compatible Debian/Ubuntu/RHEL/Rocky/Alma/Alpine/openSUSE/SLES, incluido
# containers LXC/Docker minimales sin sudo.

set -euo pipefail

# ── Trap EXIT para limpieza parcial en caso de abort ────────────────────────
INSTALL_TRAP_TRIGGERED=0
INSTALL_FINISHED_OK=0
on_exit_trap() {
    local rc=$?
    # Solo si rc != 0 Y NO terminamos OK Y NO es dry-run Y solo una vez
    if [ "$rc" -ne 0 ] \
       && [ "$INSTALL_TRAP_TRIGGERED" = "0" ] \
       && [ "$INSTALL_FINISHED_OK" = "0" ] \
       && [ "${DRY_RUN:-0}" != "1" ]; then
        INSTALL_TRAP_TRIGGERED=1
        echo "" >&2
        echo "⚠ Instalación abortada (exit code $rc) — limpieza parcial..." >&2
        # Cleanup light: detener servicio si arrancó, reportar al backend.
        # NO borramos archivos para que el cliente pueda inspeccionar y
        # ejecutar --reinstall o uninstall.sh manualmente.
        systemctl stop almc-shield 2>/dev/null || true
        # Reportar abort al backend si tenemos key + url
        if [ -n "${API_KEY:-}" ] && [ -n "${API_URL:-}" ]; then
            curl -fsS -m 3 -X POST \
                -H "Authorization: Bearer $API_KEY" \
                -H "Content-Type: application/json" \
                "$API_URL/install-event" \
                --data '{"event":"error","step_name":"'"${CURRENT_STEP:-aborted}"'","message":"Install abortado","error_code":"install_exit_'"$rc"'","hostname":"'"$(hostname 2>/dev/null || echo unknown)"'","agent_version":"'"${AGENT_VERSION:-unknown}"'"}' \
                >/dev/null 2>&1 || true
        fi
        echo "Para reintentar: curl -fsSL https://almc.es/abuse-shield/install.sh | bash -s -- --api-key=...  --reinstall" >&2
    fi
}
trap on_exit_trap EXIT

# ── Configuración fija ───────────────────────────────────────────────────────
INSTALL_DIR="/opt/almc-shield"
CONFIG_DIR="/etc/almc-shield"
STATE_DIR="/var/lib/almc-shield"
LOG_DIR="/var/log/almc-shield"
USER="almc-shield"
GROUP="almc-shield"
SERVICE_NAME="almc-shield"
AGENT_VERSION="1.0.5"
AGENT_TARBALL_URL="https://almc.es/abuse-shield-agent-${AGENT_VERSION}.tar.gz"
AGENT_TARBALL_SHA_URL="https://almc.es/abuse-shield-agent-${AGENT_VERSION}.tar.gz.sha256"
UNINSTALL_URL="https://almc.es/abuse-shield/uninstall.sh"
REPO_LATEST_API="https://api.github.com/repos/ALMC-SECURITY-SLU/Agent-Abuse-Shield/releases/latest"
TOTAL_STEPS=14

# ── Helpers de output ────────────────────────────────────────────────────────
RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[0;33m'
BLUE=$'\033[0;34m'
DIM=$'\033[2m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

# Detectar si stdout es TTY (sin colores si no lo es, ej. en pipe a un log file)
if [ ! -t 1 ]; then
  RED=""; GREEN=""; YELLOW=""; BLUE=""; DIM=""; BOLD=""; RESET=""
fi

step()    { echo ""; echo "${BOLD}${BLUE}═══${RESET} ${BOLD}$1${RESET}"; }
ok()      { echo "  ${GREEN}✓${RESET} $1"; }
warn()    { echo "  ${YELLOW}⚠${RESET}  $1" >&2; }
info()    { echo "  ${DIM}·${RESET} $1"; }
fatal()   {
    local msg="$1"; local err_code="${2:-fatal}"
    report_event "error" "${CURRENT_STEP:-unknown}" "$msg" "$err_code"
    echo ""; echo "${BOLD}${RED}✗ ERROR:${RESET} $msg" >&2; exit 1;
}

# ── Telemetría hacia el servidor central (fire-and-forget) ───────────────────
# Reporta cada paso al endpoint POST /api/v1/abuse/install-event para que el
# cliente vea el progreso en su panel /dash/abuse-shield/install. Si el endpoint
# falla, el install continúa (NO bloqueante).
CURRENT_STEP="boot"
report_event() {
    # Args: <event> <step_name> [message] [error_code]
    local event="$1" step="$2" message="${3:-}" err_code="${4:-}"
    [ -z "${API_KEY:-}" ] && return 0   # No tenemos key todavía
    [ -z "${API_URL:-}" ] && return 0
    [ "${DRY_RUN:-0}" = "1" ] && return 0  # En dry-run no spammeamos telemetría
    local hostname; hostname=$(hostname 2>/dev/null || echo "unknown")
    local os_info; os_info="${OS_PRETTY_NAME:-$(uname -s -r 2>/dev/null || echo unknown)}"
    # Escape JSON básico (sustituye " por \")
    local message_json; message_json=$(printf '%s' "$message" | sed 's/\\/\\\\/g; s/"/\\"/g; s/$/\\n/' | tr -d '\n' | sed 's/\\n$//')
    local err_json="null"; [ -n "$err_code" ] && err_json="\"$err_code\""
    local payload
    payload=$(cat <<JSON
{"event":"$event","step_name":"$step","message":"$message_json","error_code":$err_json,"hostname":"$hostname","os_info":"$os_info","agent_version":"$AGENT_VERSION"}
JSON
)
    curl -fsS -m 3 -X POST \
        -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        "$API_URL/install-event" \
        --data "$payload" \
        >/dev/null 2>&1 || true
}

# ── Helpers de detección (panel, OS, fail2ban, SELinux, disco, red) ──────────

# Verifica espacio en disco mínimo (200MB en /opt + 200MB en /var). Falla
# antes de descargar/escribir nada para evitar dejar el sistema a medias.
check_disk_space() {
    local need_mb=200
    for path in /opt /var; do
        # df -BM devuelve "200M", awk extrae sólo el número entero
        local avail
        avail=$(df -BM "$path" 2>/dev/null | awk 'NR==2 {gsub(/M/,"",$4); print $4}')
        if [ -n "$avail" ] && [ "$avail" -lt "$need_mb" ]; then
            fatal "Espacio insuficiente en $path: ${avail}MB libres (mínimo ${need_mb}MB requerido)" "disk_low_$path"
        fi
    done
}

# Verifica DNS + proxy + TLS handshake con almc.es. Falla rápido si la red
# no permitirá que el agente reporte bans (es lo que más rompe instalaciones
# en entornos corporativos con proxy/firewall estricto).
check_network_robust() {
    # 1. DNS resolution de almc.es
    local resolved=""
    if command -v getent >/dev/null 2>&1; then
        resolved=$(getent hosts almc.es 2>/dev/null | awk '{print $1}' | head -1)
    fi
    if [ -z "$resolved" ] && command -v nslookup >/dev/null 2>&1; then
        resolved=$(nslookup almc.es 2>/dev/null | awk '/^Address: / {print $2; exit}')
    fi
    if [ -z "$resolved" ] && command -v host >/dev/null 2>&1; then
        resolved=$(host almc.es 2>/dev/null | awk '/has address/ {print $4; exit}')
    fi
    if [ -z "$resolved" ]; then
        warn "DNS no pudo resolver almc.es. El install continuará pero el agente fallará al reportar."
        warn "Verifica /etc/resolv.conf, conectividad o servidores DNS internos."
    else
        info "DNS OK: almc.es → $resolved"
    fi

    # 2. Detectar proxy HTTP en el entorno (variables o /etc/environment)
    local proxy="${HTTPS_PROXY:-${https_proxy:-${HTTP_PROXY:-${http_proxy:-}}}}"
    if [ -z "$proxy" ] && [ -r /etc/environment ]; then
        # IMPORTANTE: pipe sin || true → si grep no matchea (no hay proxy) sale 1
        # y con `set -euo pipefail` el script aborta. El `|| true` evita eso.
        proxy=$(grep -E "^(https?_proxy|HTTPS?_PROXY)=" /etc/environment 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true)
    fi
    if [ -n "$proxy" ]; then
        info "Proxy HTTP detectado: $proxy"
        info "(curl + el agente Python lo usarán automáticamente via env vars)"
        report_event "step" "proxy_detected" "Proxy detectado: $proxy"
    fi

    # 3. TLS handshake check (no exige API key, solo verifica que el TLS funciona)
    # Pipefail OK aquí porque if/else captura el exit code del curl.
    if curl -fsSL -o /dev/null --max-time 8 "https://almc.es/abuse-shield/install.sh" 2>/dev/null; then
        info "TLS handshake con almc.es OK"
    else
        warn "TLS handshake con almc.es falló — el agente puede tener problemas reportando."
        warn "Posibles causas: firewall outbound 443, MITM proxy sin CA confiada, IPv6-only sin route a almc.es"
    fi
}

# Lee /etc/os-release y exporta variables OS_ID, OS_VERSION_ID, OS_PRETTY_NAME.
# Fallback si no existe el archivo (algunos containers minimal).
read_os_release() {
    if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        OS_ID="${ID:-unknown}"
        OS_VERSION_ID="${VERSION_ID:-unknown}"
        OS_PRETTY_NAME="${PRETTY_NAME:-$OS_ID $OS_VERSION_ID}"
    else
        OS_ID="unknown"
        OS_VERSION_ID="unknown"
        OS_PRETTY_NAME="$(uname -s -r 2>/dev/null || echo unknown)"
    fi
}

# Detecta presencia de paneles de control. Setea DETECTED_PANEL si encuentra.
# Importante porque algunos paneles (Plesk, cPanel) gestionan fail2ban de forma
# propia y el cliente puede borrar nuestro jail desde su UI sin querer.
detect_panel() {
    DETECTED_PANEL=""
    DETECTED_PANEL_NOTE=""
    if [ -d /usr/local/psa ] || command -v plesk >/dev/null 2>&1; then
        DETECTED_PANEL="plesk"
        DETECTED_PANEL_NOTE="Plesk gestiona fail2ban desde Tools & Settings → IP Address Banning. Si ves nuestro jail 'almc-blocklist' marcado como 'Custom', NO lo borres desde el panel — usa el panel ALMC en https://almc.es/es/dash/abuse-shield"
    elif [ -e /usr/local/cpanel/version ] || [ -d /usr/local/cpanel ]; then
        DETECTED_PANEL="cpanel"
        DETECTED_PANEL_NOTE="cPanel usa cphulkd por defecto. fail2ban estándar coexiste sin problemas si está instalado. Verifica en WHM → Security Center → cPHulk Brute Force Protection que NO interfiera con tus jails de fail2ban."
    elif [ -e /usr/local/directadmin/directadmin ] || [ -d /usr/local/directadmin ]; then
        DETECTED_PANEL="directadmin"
        DETECTED_PANEL_NOTE="DirectAdmin tiene su propio Brute Force Manager. fail2ban estándar coexiste, pero verifica /usr/local/directadmin/conf/directadmin.conf por reglas duplicadas."
    elif [ -d /www/server/panel ]; then
        DETECTED_PANEL="aapanel"
        DETECTED_PANEL_NOTE="aaPanel detectado. fail2ban se instala como módulo opcional desde Tools. Si lo gestionas desde el panel, NO borres el jail 'almc-blocklist'. El agente lo necesita."
    elif [ -d /usr/local/ispconfig ]; then
        DETECTED_PANEL="ispconfig"
        DETECTED_PANEL_NOTE="ISPConfig coexiste OK con fail2ban estándar."
    fi
}

# Verifica que fail2ban está configurado para escribir a un FILE
# (no SYSTEMD-JOURNAL ni SYSLOG). Si no, el agente nunca leerá nada.
check_fail2ban_logtarget() {
    F2B_CONF="/etc/fail2ban/fail2ban.conf"
    [ ! -f "$F2B_CONF" ] && return 0  # fail2ban no instalado todavía
    LOCAL_CONF="/etc/fail2ban/fail2ban.local"
    # Comprobar fail2ban.local primero (override), luego fail2ban.conf
    F2B_LOGTARGET=""
    for src in "$LOCAL_CONF" "$F2B_CONF"; do
        [ ! -f "$src" ] && continue
        val=$(grep -E "^[[:space:]]*logtarget[[:space:]]*=" "$src" 2>/dev/null | tail -1 | awk -F'=' '{print $2}' | tr -d ' \t\r\n')
        if [ -n "$val" ]; then
            F2B_LOGTARGET="$val"
            break
        fi
    done
    # Valores válidos para nuestro agente: una ruta absoluta a un archivo o "AUTO" (default → /var/log/fail2ban.log en Debian/RHEL)
    if [ -z "$F2B_LOGTARGET" ] || [ "$F2B_LOGTARGET" = "AUTO" ]; then
        # Default Auto = /var/log/fail2ban.log, OK
        return 0
    fi
    # Si es una ruta absoluta a archivo, OK (sobreescribimos F2B_LOG)
    if [ "${F2B_LOGTARGET:0:1}" = "/" ]; then
        F2B_LOG="$F2B_LOGTARGET"
        return 0
    fi
    # Es SYSTEMD-JOURNAL, SYSLOG, STDOUT, STDERR — NO sirve
    fatal "fail2ban está configurado con logtarget=$F2B_LOGTARGET pero el agente requiere logging a archivo.\n   Edita $F2B_CONF (o crea $LOCAL_CONF) y pon:\n     logtarget = /var/log/fail2ban.log\n   Luego: systemctl restart fail2ban && reintenta el instalador." "f2b_logtarget_invalid"
}

# Versión mínima recomendada: 0.10
check_fail2ban_version() {
    command -v fail2ban-client >/dev/null 2>&1 || return 0
    F2B_VER_FULL="$(fail2ban-client --version 2>&1 | head -1)"
    F2B_VER_NUM="$(echo "$F2B_VER_FULL" | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)"
    [ -z "$F2B_VER_NUM" ] && return 0
    F2B_MAJOR="$(echo "$F2B_VER_NUM" | cut -d. -f1)"
    F2B_MINOR="$(echo "$F2B_VER_NUM" | cut -d. -f2)"
    if [ "$F2B_MAJOR" -eq 0 ] && [ "$F2B_MINOR" -lt 10 ]; then
        warn "fail2ban versión $F2B_VER_NUM es antigua (recomendado ≥ 0.10).\n   El parser podría no entender correctamente algunos formatos de log.\n   Actualiza con: $PKG_UPDATE && $PKG_INSTALL fail2ban"
        report_event "warn" "fail2ban_version" "fail2ban $F2B_VER_NUM antigua (<0.10)" "f2b_too_old"
    fi
}

# Verifica + configura SELinux para que almc-shield pueda leer fail2ban.log.
# En RHEL/Rocky/Alma con SELinux Enforcing, los permisos POSIX están bien
# pero el contexto SELinux bloquea silenciosamente la lectura.
check_selinux_context() {
    command -v getenforce >/dev/null 2>&1 || return 0
    SE_MODE="$(getenforce 2>/dev/null || echo Disabled)"
    if [ "$SE_MODE" != "Enforcing" ]; then
        info "SELinux: $SE_MODE — no requiere configuración"
        return 0
    fi
    info "SELinux Enforcing detectado — configurando contexto para almc-shield..."
    # Auto-instalar policycoreutils-python-utils si falta semanage/restorecon
    # (en RHEL minimal o Rocky 9 cloud no vienen por default)
    if ! command -v semanage >/dev/null 2>&1 || ! command -v restorecon >/dev/null 2>&1; then
        info "semanage/restorecon faltan — instalando policycoreutils-python-utils..."
        case "$DISTRO" in
            rhel) $PKG_INSTALL policycoreutils-python-utils >/dev/null 2>&1 \
                  || $PKG_INSTALL policycoreutils-python >/dev/null 2>&1 \
                  || true ;;
        esac
    fi
    # 1. Asegurar que F2B_LOG tiene el contexto var_log_t correcto
    if command -v semanage >/dev/null 2>&1 && command -v restorecon >/dev/null 2>&1; then
        F2B_LOG="${F2B_LOG:-/var/log/fail2ban.log}"
        if [ -f "$F2B_LOG" ]; then
            semanage fcontext -a -t var_log_t "$F2B_LOG" 2>/dev/null \
                || semanage fcontext -m -t var_log_t "$F2B_LOG" 2>/dev/null \
                || true
            restorecon -v "$F2B_LOG" 2>/dev/null || true
        fi
        # Contexto de los binarios del agente
        if [ -d "$INSTALL_DIR" ]; then
            semanage fcontext -a -t bin_t "${INSTALL_DIR}/bin/almc-shield" 2>/dev/null || true
            restorecon -RFv "$INSTALL_DIR" 2>/dev/null || true
        fi
        # Permitir que un daemon haga sudo (necesario para fail2ban-client banip)
        setsebool -P daemons_use_tty 1 2>/dev/null || true
        ok "SELinux contexto configurado (var_log_t + bin_t)"
        report_event "step" "selinux_setup" "SELinux Enforcing: contexto configurado"
    else
        warn "SELinux Enforcing pero semanage/restorecon no instalados.\n   Instala con: $PKG_INSTALL policycoreutils-python-utils\n   Y reejecuta el instalador, o el agente NO podrá leer $F2B_LOG."
        report_event "warn" "selinux_setup" "SELinux Enforcing sin semanage" "selinux_no_semanage"
    fi
}

# Health check E2E post-install: POST report ficticio + verificar que aparece.
# Confirma que el flow completo funciona antes de declarar "install OK".
health_check_e2e() {
    [ "${SKIP_HEALTH_CHECK:-0}" = "1" ] && { info "Health check saltado (--skip-health-check)"; return 0; }
    info "Esperando 3s para que el agente envíe primer heartbeat..."
    sleep 3
    # Ya hicimos POST heartbeat al inicio. Ahora basta con confirmar que el
    # servicio esté UP y haya emitido logs estructurados.
    if ! command -v systemctl >/dev/null 2>&1 || [ ! -d "/run/systemd/system" ]; then
        info "Sin systemd → skip health check"
        return 0
    fi
    if ! systemctl is-active --quiet almc-shield.service; then
        warn "almc-shield no está activo — health check ABORTADO"
        report_event "warn" "health_check" "service inactive en health check"
        return 1
    fi
    # Verifica que el agent ha producido algún log (señal de que arrancó OK)
    if [ -f "$LOG_DIR/agent.log" ]; then
        LOG_LINES=$(wc -l < "$LOG_DIR/agent.log" 2>/dev/null || echo 0)
        if [ "$LOG_LINES" -gt 0 ]; then
            ok "Agente activo · ${LOG_LINES} líneas en $LOG_DIR/agent.log"
        else
            warn "Agente activo pero $LOG_DIR/agent.log vacío (puede tardar 5-10s en arrancar)"
        fi
    fi
    # Verifica que un POST al backend desde el host responde 200 (NO usa la key del agente,
    # sino la del install para reconfirmar conectividad post-install)
    HC_CODE=$(curl -fsSL -o /dev/null -w "%{http_code}" --max-time 10 \
                -H "Authorization: Bearer $API_KEY" \
                -H "Content-Type: application/json" \
                -X POST "$API_URL/heartbeat" \
                --data '{"agent_version":"'"$AGENT_VERSION"'","hostname":"'"$(hostname)"'","stats":{"local_bans":0,"queue_pending":0,"health_check":true}}' \
                2>/dev/null || echo "000")
    if [ "$HC_CODE" = "200" ]; then
        ok "Health check E2E: backend responde 200 OK"
        report_event "step" "health_check" "E2E health check passed (200)"
    else
        warn "Health check post-install: backend responde HTTP $HC_CODE"
        report_event "warn" "health_check" "E2E health check HTTP $HC_CODE" "hc_http_$HC_CODE"
    fi
}

# Crea systemd timer que checkea releases nuevos en GitHub semanalmente.
setup_auto_update_timer() {
    [ ! -d /run/systemd/system ] && { warn "Sin systemd → --auto-update no se puede configurar"; return 0; }
    info "Instalando timer systemd de auto-update (semanal, domingo 03:00)..."
    cat > /etc/systemd/system/almc-shield-update.service <<EOF
[Unit]
Description=ALMC Abuse Shield agent — check for updates
Documentation=https://github.com/ALMC-SECURITY-SLU/Agent-Abuse-Shield
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'LATEST=\$(curl -fsSL ${REPO_LATEST_API} | grep -oE "\"tag_name\":[[:space:]]*\"v[^\"]+" | head -1 | awk -F\\" "{print \\\$4}" | sed "s/^v//"); [ -z "\$LATEST" ] && exit 0; CURRENT="${AGENT_VERSION}"; if [ "\$LATEST" != "\$CURRENT" ]; then logger -t almc-shield-update "New version \$LATEST available (current \$CURRENT) — running installer"; curl -fsSL https://almc.es/abuse-shield/install.sh | API_KEY_CACHE=\$(cat /etc/almc-shield/config.ini | grep -E "^api_key" | head -1 | cut -d= -f2 | tr -d " ") ABUSE_SHIELD_API_KEY="\$API_KEY_CACHE" bash -s -- --reinstall --skip-health-check; fi'
EOF

    cat > /etc/systemd/system/almc-shield-update.timer <<'EOF'
[Unit]
Description=ALMC Abuse Shield — weekly update check
Documentation=https://github.com/ALMC-SECURITY-SLU/Agent-Abuse-Shield

[Timer]
OnCalendar=Sun 03:00:00
RandomizedDelaySec=2h
Persistent=true

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable --now almc-shield-update.timer >/dev/null 2>&1 || true
    ok "Auto-update timer activo (semanal · domingo 03:00 ± 2h)"
    report_event "step" "auto_update" "Auto-update timer instalado"
}

# ── Args + env vars ──────────────────────────────────────────────────────────
API_KEY="${ABUSE_SHIELD_API_KEY:-}"
API_URL="${ABUSE_SHIELD_API_URL:-https://almc.es/api/v1/abuse}"
SKIP_FAIL2BAN_INSTALL="${ABUSE_SHIELD_SKIP_FAIL2BAN_INSTALL:-0}"
SKIP_API_TEST="${ABUSE_SHIELD_SKIP_API_TEST:-0}"
SKIP_JAIL_RELOAD="${ABUSE_SHIELD_SKIP_JAIL_RELOAD:-0}"
SKIP_HEALTH_CHECK="${ABUSE_SHIELD_SKIP_HEALTH_CHECK:-0}"
SKIP_SELINUX="${ABUSE_SHIELD_SKIP_SELINUX:-0}"
FORCE_PERMISSIONS="${ABUSE_SHIELD_FORCE_PERMISSIONS:-0}"
DRY_RUN="${ABUSE_SHIELD_DRY_RUN:-0}"
REINSTALL="${ABUSE_SHIELD_REINSTALL:-0}"
AUTO_UPDATE="${ABUSE_SHIELD_AUTO_UPDATE:-0}"
LOCAL_TARBALL=""

for arg in "$@"; do
  case "$arg" in
    --api-key=*)             API_KEY="${arg#*=}" ;;
    --api-url=*)             API_URL="${arg#*=}" ;;
    --tarball=*)             LOCAL_TARBALL="${arg#*=}" ;;
    --skip-fail2ban-install) SKIP_FAIL2BAN_INSTALL=1 ;;
    --skip-api-test)         SKIP_API_TEST=1 ;;
    --skip-jail-reload)      SKIP_JAIL_RELOAD=1 ;;
    --skip-health-check)     SKIP_HEALTH_CHECK=1 ;;
    --skip-selinux)          SKIP_SELINUX=1 ;;
    --force-permissions)     FORCE_PERMISSIONS=1 ;;
    --dry-run)               DRY_RUN=1 ;;
    --reinstall)             REINSTALL=1 ;;
    --auto-update)           AUTO_UPDATE=1 ;;
    --no-color)              RED=""; GREEN=""; YELLOW=""; BLUE=""; DIM=""; BOLD=""; RESET="" ;;
    -h|--help)
      cat <<USAGE
ALMC Abuse Shield agent installer (v${AGENT_VERSION}).

REQUIRED:
  --api-key=KEY              tu API key (formato ab_live_*)
                             o env var ABUSE_SHIELD_API_KEY

OPTIONAL:
  --api-url=URL              override del endpoint
                             (env ABUSE_SHIELD_API_URL, default almc.es/api/v1/abuse)
  --dry-run                  ejecuta solo checks de detección — no instala nada
  --reinstall                desinstala primero (uninstall.sh) y luego reinstala
  --tarball=PATH             usa tarball local (offline / airgapped)
  --auto-update              instala timer systemd para auto-update semanal

  --skip-fail2ban-install    no instalar fail2ban automáticamente si falta
  --skip-api-test            no verificar conectividad con el API antes de arrancar
  --skip-jail-reload         no recargar fail2ban tras copiar la config del jail
  --skip-health-check        no ejecutar el health check E2E al final
  --skip-selinux             no auto-configurar SELinux contexto
  --force-permissions        forzar fix de permisos en /var/log/fail2ban.log
  --no-color                 desactiva colores ANSI

EXAMPLES:
  install.sh --api-key=ab_live_XXX
  install.sh --api-key=ab_live_XXX --dry-run                 # pre-flight check
  install.sh --api-key=ab_live_XXX --reinstall               # limpia + reinstala
  install.sh --api-key=ab_live_XXX --auto-update             # con timer update
  install.sh --api-key=ab_live_XXX --tarball=/tmp/agent.tgz  # airgapped
  curl -fsSL https://almc.es/abuse-shield/install.sh | bash -s -- --api-key=ab_live_XXX
  curl -fsSL https://almc.es/abuse-shield/install.sh | ABUSE_SHIELD_API_KEY=ab_live_XXX bash

VERIFICACIÓN DE INTEGRIDAD (sidecar SHA-256 del tarball):
  # El install.sh automáticamente verifica el SHA-256 del tarball que descarga.
  # Verificación manual del tarball:
  curl -fsSL https://almc.es/abuse-shield-agent-${AGENT_VERSION}.tar.gz -o agent.tgz
  curl -fsSL https://almc.es/abuse-shield-agent-${AGENT_VERSION}.tar.gz.sha256 -o agent.tgz.sha256
  sha256sum -c agent.tgz.sha256

DOCS:
  Panel:  https://almc.es/es/dash/abuse-shield
  API key: https://almc.es/es/dash/abuse-shield/settings
  Código:  https://github.com/ALMC-SECURITY-SLU/Agent-Abuse-Shield
USAGE
      exit 0
      ;;
  esac
done

# ── Validación API key ───────────────────────────────────────────────────────
if [ -z "$API_KEY" ]; then
  cat >&2 <<EOF

${BOLD}${RED}✗ ERROR:${RESET} No se proporcionó API key.

Pasa la key vía argumento o variable de entorno:

  ${BOLD}install.sh --api-key=ab_live_XXXXXXX${RESET}
  ${BOLD}ABUSE_SHIELD_API_KEY=ab_live_XXXXXXX install.sh${RESET}
  ${BOLD}curl -fsSL https://almc.es/abuse-shield/install.sh | ABUSE_SHIELD_API_KEY=ab_live_XXX bash${RESET}

Obtén tu API key en ${BOLD}https://almc.es/es/dash/abuse-shield/settings${RESET}
EOF
  exit 1
fi

if [[ ! "$API_KEY" =~ ^ab_(live|test)_[A-Za-z0-9]{16,}$ ]]; then
  fatal "Formato de API key inválido (esperado: ab_live_xxx o ab_test_xxx, longitud ≥ 24)"
fi

# ── Detección root + sudo ────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    cat >&2 <<EOF

${BOLD}${RED}✗ ERROR:${RESET} este script necesita ejecutarse como root.

Opciones:
  ${BOLD}curl -fsSL https://almc.es/abuse-shield/install.sh | sudo bash -s -- --api-key=ab_live_XXX${RESET}
  ${BOLD}sudo -E ABUSE_SHIELD_API_KEY=ab_live_XXX bash -c "curl -fsSL https://almc.es/abuse-shield/install.sh | bash"${RESET}
EOF
  else
    cat >&2 <<EOF

${BOLD}${RED}✗ ERROR:${RESET} no eres root y \`sudo\` no está instalado.

Conviértete en root con:
  ${BOLD}su -${RESET}

Y vuelve a ejecutar el script.
EOF
  fi
  exit 1
fi

# ── Banner inicial (con OS_PRETTY_NAME) ──────────────────────────────────────
read_os_release

MODE_LINE=""
[ "$DRY_RUN" = "1" ] && MODE_LINE="${BOLD}${YELLOW}  MODO: --dry-run · NO modificará el sistema${RESET}"
[ "$REINSTALL" = "1" ] && MODE_LINE="${BOLD}${YELLOW}  MODO: --reinstall · desinstalará primero${RESET}"

cat <<EOF

${BOLD}${BLUE}╔══════════════════════════════════════════════════════════════╗${RESET}
${BOLD}${BLUE}║${RESET}   ${BOLD}ALMC Abuse Shield Agent${RESET} — installer v${AGENT_VERSION}              ${BOLD}${BLUE}║${RESET}
${BOLD}${BLUE}╚══════════════════════════════════════════════════════════════╝${RESET}

  API URL:  ${API_URL}
  API key:  ${API_KEY:0:14}...${API_KEY: -4}
  Host:     $(hostname)
  OS:       ${OS_PRETTY_NAME}
  Kernel:   $(uname -s -r)
${MODE_LINE}

EOF

# ── --reinstall: limpieza previa ─────────────────────────────────────────────
if [ "$REINSTALL" = "1" ] && [ "$DRY_RUN" != "1" ]; then
  step "Pre-paso — Desinstalación previa (--reinstall)"
  if curl -fsSL -m 10 "$UNINSTALL_URL" -o /tmp/almc-shield-uninstall.sh 2>/dev/null; then
    chmod +x /tmp/almc-shield-uninstall.sh
    bash /tmp/almc-shield-uninstall.sh || warn "uninstall.sh terminó con errores — continuamos igual"
    rm -f /tmp/almc-shield-uninstall.sh
    ok "Desinstalación previa completada"
  else
    warn "No se pudo descargar uninstall.sh — intentando limpieza manual mínima..."
    systemctl stop almc-shield 2>/dev/null || true
    systemctl disable almc-shield 2>/dev/null || true
    rm -rf /opt/almc-shield /etc/almc-shield /var/lib/almc-shield /var/log/almc-shield
    rm -f /etc/systemd/system/almc-shield.service /etc/systemd/system/almc-shield-update.service /etc/systemd/system/almc-shield-update.timer
    rm -f /etc/sudoers.d/almc-shield /usr/local/bin/almc-shield
    rm -f /etc/fail2ban/jail.d/almc-blocklist.conf /etc/fail2ban/filter.d/almc-blocklist.conf
    systemctl daemon-reload 2>/dev/null || true
    userdel almc-shield 2>/dev/null || deluser almc-shield 2>/dev/null || true
    ok "Limpieza manual mínima OK"
  fi
fi

# ── Reportar inicio del install ──────────────────────────────────────────────
report_event "start" "boot" "Install v${AGENT_VERSION} iniciado en $(hostname) [${OS_PRETTY_NAME}]"

# ═══════════════════════════════════════════════════════════════════════════
# Pre-flight: disk space + network robust (antes de cualquier modificación)
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="preflight"
step "Pre-flight — espacio en disco + DNS + proxy"
if [ "$DRY_RUN" = "1" ]; then
  info "(dry-run) check disk + network sí se ejecutan (read-only)"
fi
check_disk_space
check_network_robust

# ═══════════════════════════════════════════════════════════════════════════
# PASO 1: Detección de distro (apt/dnf/yum/apk/zypper)
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="distro_detect"
step "1/${TOTAL_STEPS} — Detectando sistema"
if command -v apt-get >/dev/null 2>&1; then
  DISTRO="debian"
  PKG_INSTALL="apt-get install -y -qq"
  PKG_UPDATE="apt-get update -qq"
elif command -v dnf >/dev/null 2>&1; then
  DISTRO="rhel"
  PKG_INSTALL="dnf install -y -q"
  PKG_UPDATE=":"
elif command -v yum >/dev/null 2>&1; then
  DISTRO="rhel"
  PKG_INSTALL="yum install -y -q"
  PKG_UPDATE=":"
elif command -v zypper >/dev/null 2>&1; then
  DISTRO="suse"
  PKG_INSTALL="zypper --non-interactive --quiet install"
  PKG_UPDATE="zypper --non-interactive --quiet refresh"
elif command -v apk >/dev/null 2>&1; then
  DISTRO="alpine"
  PKG_INSTALL="apk add --no-cache --quiet"
  PKG_UPDATE=":"
else
  fatal "Distro no soportada (no se encontró apt/dnf/yum/zypper/apk)."
fi
ok "Distro detectada: ${BOLD}${DISTRO}${RESET} (${OS_PRETTY_NAME})"
report_event "step" "distro_detect" "Distro family: $DISTRO · $OS_PRETTY_NAME"

# ═══════════════════════════════════════════════════════════════════════════
# PASO 2: Detección de panel de control (Plesk/cPanel/DA/aaPanel/ISPConfig)
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="panel_detect"
step "2/${TOTAL_STEPS} — Detectando panel de control"
detect_panel
if [ -n "$DETECTED_PANEL" ]; then
  warn "Panel detectado: ${BOLD}${DETECTED_PANEL}${RESET}"
  info "$DETECTED_PANEL_NOTE"
  report_event "warn" "panel_detect" "Panel detectado: $DETECTED_PANEL" "panel_$DETECTED_PANEL"
else
  ok "Sin panel de control detectado — instalación estándar"
  report_event "step" "panel_detect" "Sin panel de control"
fi

# ═══════════════════════════════════════════════════════════════════════════
# PASO 3: Pre-flight de conectividad
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="api_test"
step "3/${TOTAL_STEPS} — Validando conectividad con el servidor central"
if ! command -v curl >/dev/null 2>&1; then
  warn "curl no está instalado, instalándolo..."
  [ "$DRY_RUN" = "1" ] && info "(dry-run · saltado)" || {
    $PKG_UPDATE >/dev/null 2>&1 || true
    $PKG_INSTALL curl >/dev/null 2>&1 || fatal "No se pudo instalar curl"
  }
fi

if [ "$SKIP_API_TEST" != "1" ]; then
  HTTP_CODE=$(curl -fsSL -o /dev/null -w "%{http_code}" --max-time 10 \
              -H "Authorization: Bearer $API_KEY" \
              -H "Content-Type: application/json" \
              -X POST "$API_URL/heartbeat" \
              --data '{"agent_version":"'"$AGENT_VERSION"'","hostname":"'"$(hostname)"'","stats":{"local_bans":0,"queue_pending":0}}' \
              2>/dev/null || true)
  case "$HTTP_CODE" in
    200) ok "API responde 200 ${GREEN}OK${RESET} — credenciales válidas"
         report_event "step" "api_test" "API auth OK (200)" ;;
    401) fatal "API responde 401 ${RED}UNAUTHORIZED${RESET}. La API key no es reconocida.\n   Verifica en https://almc.es/es/dash/abuse-shield/settings" "http_401" ;;
    403) fatal "API responde 403 ${RED}FORBIDDEN${RESET}. Tu tenant puede estar suspendido o has excedido la cuota.\n   Contacta soporte: soporte@almc.es" "http_403" ;;
    429) warn "API responde 429 ${YELLOW}RATE LIMITED${RESET}. Continuamos — el agente reintentará."
         report_event "warn" "api_test" "Rate limited (429), continuamos" "http_429" ;;
    000) fatal "No hay conectividad con ${API_URL}.\n   Verifica DNS/firewall/proxy en este host (egress HTTPS:443 a almc.es)." "no_connectivity" ;;
    *)   warn "API responde HTTP ${HTTP_CODE} (inesperado). Continuamos — el agente lo gestionará en runtime."
         report_event "warn" "api_test" "API responde HTTP $HTTP_CODE inesperado" "http_$HTTP_CODE" ;;
  esac
else
  info "API test saltado (--skip-api-test)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# PASO 4: Python 3.8+ + venv
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="python_check"
step "4/${TOTAL_STEPS} — Python 3.8+ con módulo venv"
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    VER=$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")
    MAJOR=${VER%.*}
    MINOR=${VER#*.}
    if [ "$MAJOR" = "3" ] && [ "$MINOR" -ge 8 ]; then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  info "Python 3.8+ no encontrado, instalando..."
  [ "$DRY_RUN" = "1" ] && info "(dry-run · saltado)" || {
    $PKG_UPDATE >/dev/null 2>&1 || true
    case "$DISTRO" in
      debian) $PKG_INSTALL python3 python3-venv >/dev/null 2>&1 ;;
      rhel)   $PKG_INSTALL python3 python3-pip >/dev/null 2>&1 ;;
      suse)   $PKG_INSTALL python3 python3-pip python3-virtualenv >/dev/null 2>&1 ;;
      alpine) $PKG_INSTALL python3 py3-pip >/dev/null 2>&1 ;;
    esac
    PYTHON_BIN="python3"
  }
fi

[ "$DRY_RUN" = "1" ] && PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  fatal "No se pudo instalar Python 3.8+. Instálalo manualmente y reintenta." "python_missing"
fi
PYTHON_VERSION_STR="$($PYTHON_BIN --version 2>&1)"
ok "$PYTHON_VERSION_STR"
report_event "step" "python_check" "$PYTHON_VERSION_STR"

# Validar que ensurepip está disponible (Debian/Ubuntu separan python3 vs python3-venv)
if [ "$DRY_RUN" != "1" ]; then
  if ! "$PYTHON_BIN" -c 'import ensurepip' >/dev/null 2>&1; then
    info "ensurepip no disponible — instalando python3-venv..."
    case "$DISTRO" in
      debian)
        PYTHON_MINOR=$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
        $PKG_UPDATE >/dev/null 2>&1 || true
        $PKG_INSTALL python3-venv "python${PYTHON_MINOR}-venv" >/dev/null 2>&1 \
          || $PKG_INSTALL python3-venv >/dev/null 2>&1 \
          || fatal "No se pudo instalar python3-venv. Ejecuta manualmente: apt install python3-venv"
        ;;
      rhel)   $PKG_INSTALL python3-pip >/dev/null 2>&1 || true ;;
      suse)   $PKG_INSTALL python3-virtualenv python3-pip >/dev/null 2>&1 || true ;;
      alpine) $PKG_INSTALL py3-virtualenv >/dev/null 2>&1 || true ;;
    esac
    if ! "$PYTHON_BIN" -c 'import ensurepip' >/dev/null 2>&1; then
      fatal "ensurepip sigue sin estar disponible. Instala manualmente python3-venv y reintenta." "ensurepip_missing"
    fi
    ok "python3-venv instalado"
    report_event "step" "venv_module" "python3-venv instalado automáticamente"
  else
    ok "venv listo"
    report_event "step" "venv_module" "venv ya disponible"
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# PASO 5: Usuario sistema
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="user_create"
step "5/${TOTAL_STEPS} — Usuario sistema $USER"
if [ "$DRY_RUN" = "1" ]; then
  if getent passwd "$USER" >/dev/null 2>&1; then
    info "(dry-run) usuario $USER ya existe"
  else
    info "(dry-run) usuario $USER se crearía"
  fi
else
  if ! getent passwd "$USER" >/dev/null 2>&1; then
    if [ "$DISTRO" = "alpine" ]; then
      adduser -S -D -H -s /sbin/nologin "$USER"
    else
      useradd --system --no-create-home --shell /usr/sbin/nologin "$USER"
    fi
    ok "Usuario creado"
    report_event "step" "user_create" "Usuario almc-shield creado"
  else
    ok "Usuario ya existe"
    report_event "step" "user_create" "Usuario almc-shield ya existía"
  fi

  # Añadir al grupo 'adm' (puede leer /var/log/* por defecto en Debian/Ubuntu)
  if getent group adm >/dev/null 2>&1; then
    usermod -a -G adm "$USER" 2>/dev/null \
      || addgroup "$USER" adm 2>/dev/null \
      || true
    ok "Añadido al grupo 'adm' (para leer /var/log/fail2ban.log)"
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# PASO 6: Directorios
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="dirs_create"
step "6/${TOTAL_STEPS} — Creando directorios"
if [ "$DRY_RUN" = "1" ]; then
  info "(dry-run) crearía: $INSTALL_DIR, $CONFIG_DIR, $STATE_DIR, $LOG_DIR"
else
  mkdir -p "$INSTALL_DIR/bin" "$INSTALL_DIR/lib" "$INSTALL_DIR/share"
  mkdir -p "$CONFIG_DIR" "$STATE_DIR" "$LOG_DIR"
  chown root:root "$INSTALL_DIR" "$CONFIG_DIR"
  chmod 755 "$INSTALL_DIR" "$CONFIG_DIR"
  chown "$USER:$GROUP" "$STATE_DIR" "$LOG_DIR"
  chmod 700 "$STATE_DIR"
  chmod 750 "$LOG_DIR"
  ok "Directorios listos: $INSTALL_DIR, $CONFIG_DIR, $STATE_DIR, $LOG_DIR"
  report_event "step" "dirs_create" "Directorios creados"
fi

# ═══════════════════════════════════════════════════════════════════════════
# PASO 7: Descarga o copia del paquete (con soporte --tarball local)
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="package_download"
step "7/${TOTAL_STEPS} — Obteniendo el paquete del agente"
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
fi

if [ "$DRY_RUN" = "1" ]; then
  if [ -n "$LOCAL_TARBALL" ]; then
    [ -r "$LOCAL_TARBALL" ] && info "(dry-run) usaría tarball local: $LOCAL_TARBALL ($(du -h "$LOCAL_TARBALL" | cut -f1))" \
                            || warn "(dry-run) tarball local NO existe: $LOCAL_TARBALL"
  elif [ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/almc_shield" ]; then
    info "(dry-run) copiaría desde fuente local: $SCRIPT_DIR"
  else
    info "(dry-run) descargaría tarball desde $AGENT_TARBALL_URL"
  fi
elif [ -n "$LOCAL_TARBALL" ]; then
  [ ! -r "$LOCAL_TARBALL" ] && fatal "Tarball local no legible: $LOCAL_TARBALL" "tarball_not_readable"
  info "Usando tarball local: $LOCAL_TARBALL"
  TMP_EXTRACT="$(mktemp -d -t almc-shield-extract.XXXXXX)"
  tar -xzf "$LOCAL_TARBALL" -C "$TMP_EXTRACT" || fatal "Tarball local corrupto" "tarball_corrupt"
  # Detecta si el tarball tiene un directorio root o no
  if [ -d "$TMP_EXTRACT/almc_shield" ]; then SRC="$TMP_EXTRACT"
  else SRC="$(find "$TMP_EXTRACT" -maxdepth 2 -type d -name almc_shield | head -1 | xargs dirname 2>/dev/null)"
  fi
  [ -z "$SRC" ] && fatal "Tarball local no contiene almc_shield/" "tarball_invalid_structure"
  cp -r "$SRC/almc_shield" "$INSTALL_DIR/lib/"
  cp "$SRC/requirements.txt" "$INSTALL_DIR/"
  cp "$SRC/pyproject.toml" "$INSTALL_DIR/"
  cp -r "$SRC/share/." "$INSTALL_DIR/share/"
  [ -f "$SRC/config.ini.example" ] && cp "$SRC/config.ini.example" "$INSTALL_DIR/share/config.ini.example"
  rm -rf "$TMP_EXTRACT"
  SCRIPT_DIR="$INSTALL_DIR/share"
  ok "Paquete extraído desde tarball local"
elif [ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/almc_shield" ]; then
  info "Copia desde fuente local: $SCRIPT_DIR"
  cp -r "$SCRIPT_DIR/almc_shield" "$INSTALL_DIR/lib/"
  cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
  cp "$SCRIPT_DIR/pyproject.toml" "$INSTALL_DIR/"
  cp -r "$SCRIPT_DIR/share/." "$INSTALL_DIR/share/"
  [ -f "$SCRIPT_DIR/config.ini.example" ] && cp "$SCRIPT_DIR/config.ini.example" "$INSTALL_DIR/share/config.ini.example"
  ok "Paquete copiado de fuente local"
else
  info "Descarga del tarball desde $AGENT_TARBALL_URL"
  TMP_TAR="$(mktemp -t almc-shield-agent.XXXXXX.tar.gz)"
  trap 'rm -f "$TMP_TAR"' EXIT
  if ! curl -fsSL "$AGENT_TARBALL_URL" -o "$TMP_TAR"; then
    fatal "Descarga falló desde $AGENT_TARBALL_URL.\n   Verifica conectividad a almc.es:443." "download_failed"
  fi
  ok "Tarball descargado: $(du -h "$TMP_TAR" | cut -f1)"

  # Verificar SHA-256 si el sidecar existe (opcional pero recomendado)
  TMP_SHA="$(mktemp -t almc-shield-sha.XXXXXX)"
  if curl -fsSL --max-time 5 "$AGENT_TARBALL_SHA_URL" -o "$TMP_SHA" 2>/dev/null && [ -s "$TMP_SHA" ]; then
    EXPECTED_SHA="$(head -1 "$TMP_SHA" | awk '{print $1}')"
    ACTUAL_SHA="$(sha256sum "$TMP_TAR" | awk '{print $1}')"
    if [ "$EXPECTED_SHA" = "$ACTUAL_SHA" ]; then
      ok "SHA-256 verificado: $EXPECTED_SHA"
      report_event "step" "sha256_verify" "tarball SHA-256 OK"
    else
      rm -f "$TMP_TAR"
      fatal "SHA-256 NO coincide!\n   Esperado: $EXPECTED_SHA\n   Recibido: $ACTUAL_SHA" "sha256_mismatch"
    fi
  else
    info "Sidecar .sha256 no disponible — saltando verificación (no es bloqueante)"
  fi
  rm -f "$TMP_SHA"

  TMP_EXTRACT="$(mktemp -d -t almc-shield-extract.XXXXXX)"
  tar -xzf "$TMP_TAR" -C "$TMP_EXTRACT"
  cp -r "$TMP_EXTRACT/almc_shield" "$INSTALL_DIR/lib/"
  cp "$TMP_EXTRACT/requirements.txt" "$INSTALL_DIR/"
  cp "$TMP_EXTRACT/pyproject.toml" "$INSTALL_DIR/"
  cp -r "$TMP_EXTRACT/share/." "$INSTALL_DIR/share/"
  cp "$TMP_EXTRACT/config.ini.example" "$INSTALL_DIR/share/config.ini.example"
  rm -rf "$TMP_EXTRACT"
  SCRIPT_DIR="$INSTALL_DIR/share"
  ok "Paquete extraído"
fi
[ "$DRY_RUN" != "1" ] && report_event "step" "package_download" "Paquete descargado y extraído"

# ═══════════════════════════════════════════════════════════════════════════
# PASO 8: venv + dependencias
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="venv_setup"
step "8/${TOTAL_STEPS} — Python venv + dependencias"
if [ "$DRY_RUN" = "1" ]; then
  info "(dry-run) crearía venv en $INSTALL_DIR/venv + instalaría requirements.txt"
else
  if [ ! -d "$INSTALL_DIR/venv" ]; then
    "$PYTHON_BIN" -m venv "$INSTALL_DIR/venv" || fatal "Fallo creando venv. Revisa que python3-venv esté instalado." "venv_create_failed"
  fi
  "$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet --disable-pip-version-check \
      || fatal "Fallo actualizando pip dentro del venv" "pip_upgrade_failed"
  "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet --disable-pip-version-check \
      || fatal "Fallo instalando dependencias Python (httpx/watchdog/structlog)" "pip_install_failed"
  ok "venv listo en $INSTALL_DIR/venv"
  report_event "step" "venv_setup" "venv + dependencias instaladas"

  # Entrypoint
  cat > "$INSTALL_DIR/bin/almc-shield" <<EOF
#!/bin/bash
exec "$INSTALL_DIR/venv/bin/python3" -m almc_shield "\$@"
EOF
  chmod 755 "$INSTALL_DIR/bin/almc-shield"
  ln -sf "$INSTALL_DIR/bin/almc-shield" /usr/local/bin/almc-shield
  ok "Entrypoint /usr/local/bin/almc-shield instalado"

  # Configurar venv para encontrar almc_shield
  PY_MINOR=$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  echo "$INSTALL_DIR/lib" > "$INSTALL_DIR/venv/lib/python${PY_MINOR}/site-packages/almc-shield.pth"
fi

# ═══════════════════════════════════════════════════════════════════════════
# PASO 9: config.ini
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="config_generate"
step "9/${TOTAL_STEPS} — Generando configuración"
if [ "$DRY_RUN" = "1" ]; then
  info "(dry-run) escribiría $CONFIG_DIR/config.ini (chmod 600 owner $USER)"
elif [ ! -f "$CONFIG_DIR/config.ini" ]; then
  cp "$SCRIPT_DIR/config.ini.example" "$CONFIG_DIR/config.ini"
  ESCAPED_KEY=$(printf '%s\n' "$API_KEY" | sed 's/[\/&]/\\&/g')
  ESCAPED_URL=$(printf '%s\n' "$API_URL" | sed 's/[\/&]/\\&/g')
  sed -i "s|\${API_KEY}|$ESCAPED_KEY|g" "$CONFIG_DIR/config.ini"
  sed -i "s|^url = .*|url = $ESCAPED_URL|g" "$CONFIG_DIR/config.ini"
  chown "$USER:$GROUP" "$CONFIG_DIR/config.ini"
  chmod 600 "$CONFIG_DIR/config.ini"
  ok "config.ini creado (chmod 600, owner $USER)"
  report_event "step" "config_generate" "config.ini generado en $CONFIG_DIR"
else
  ok "config.ini ya existe — preservado (no se sobrescribe)"
  info "Si quieres regenerarlo: rm $CONFIG_DIR/config.ini && reejecuta el instalador"
  report_event "step" "config_generate" "config.ini preservado (ya existía)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# PASO 10: fail2ban (instalación + version check + logtarget check + perms)
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="fail2ban_setup"
step "10/${TOTAL_STEPS} — fail2ban (instalación + version + logtarget + perms)"
if ! command -v fail2ban-client >/dev/null 2>&1; then
  if [ "$SKIP_FAIL2BAN_INSTALL" = "1" ]; then
    warn "fail2ban-client no instalado y --skip-fail2ban-install activo.\n   El agente arrancará pero no podrá reportar nada hasta que instales fail2ban."
  elif [ "$DRY_RUN" = "1" ]; then
    info "(dry-run) instalaría fail2ban con $PKG_INSTALL"
  else
    info "fail2ban no instalado, instalándolo..."
    $PKG_UPDATE >/dev/null 2>&1 || true
    case "$DISTRO" in
      debian)   $PKG_INSTALL fail2ban >/dev/null 2>&1 ;;
      rhel)
        # En RHEL/Rocky/Alma 8+ fail2ban vive en EPEL. Instalamos EPEL primero
        # de forma idempotente. dnf install epel-release es no-op si ya está.
        if ! rpm -q epel-release >/dev/null 2>&1; then
          info "Instalando EPEL (repo del que viene fail2ban en RHEL)..."
          $PKG_INSTALL epel-release >/dev/null 2>&1 || warn "EPEL no se pudo instalar"
        fi
        $PKG_INSTALL fail2ban >/dev/null 2>&1
        ;;
      suse)     $PKG_INSTALL fail2ban >/dev/null 2>&1 ;;
      alpine)   $PKG_INSTALL fail2ban >/dev/null 2>&1 ;;
    esac
    if command -v fail2ban-client >/dev/null 2>&1; then
      ok "fail2ban instalado"
      systemctl enable --now fail2ban >/dev/null 2>&1 || true
      report_event "step" "fail2ban_setup" "fail2ban instalado y habilitado automáticamente"
    else
      warn "No se pudo instalar fail2ban automáticamente. Instálalo manualmente cuando puedas."
      report_event "warn" "fail2ban_setup" "No se pudo auto-instalar fail2ban" "f2b_install_failed"
    fi
  fi
else
  F2B_VER_FULL=$(fail2ban-client --version 2>&1 | head -1)
  ok "fail2ban-client ya instalado: $F2B_VER_FULL"
  report_event "step" "fail2ban_setup" "fail2ban ya instalado: $F2B_VER_FULL"
fi

# Version min check (≥0.10 recomendado)
check_fail2ban_version

# logtarget check (debe ser FILE/AUTO, no SYSTEMD-JOURNAL ni SYSLOG)
check_fail2ban_logtarget

# Permisos /var/log/fail2ban.log — el agente necesita read access
F2B_LOG="${F2B_LOG:-/var/log/fail2ban.log}"
if [ -f "$F2B_LOG" ] && [ "$DRY_RUN" != "1" ]; then
  if ! sudo -u "$USER" -n test -r "$F2B_LOG" 2>/dev/null; then
    chgrp adm "$F2B_LOG" 2>/dev/null || true
    chmod 640 "$F2B_LOG" 2>/dev/null || true
    ok "Permisos $F2B_LOG ajustados a 640 root:adm"
  else
    ok "Permisos $F2B_LOG OK (legible por el agente)"
  fi

  # Logrotate — asegurar que la rotación mantiene 640 root:adm
  LOGROTATE_F2B="/etc/logrotate.d/fail2ban"
  if [ ! -f "$LOGROTATE_F2B" ] || ! grep -q "create 640 root adm" "$LOGROTATE_F2B" 2>/dev/null; then
    cat > "$LOGROTATE_F2B" <<'LOGROTATE'
/var/log/fail2ban.log {
        weekly
        rotate 4
        missingok
        compress
        delaycompress
        postrotate
                /usr/bin/fail2ban-client flushlogs 1>/dev/null 2>&1 || true
        endscript
        create 640 root adm
}
LOGROTATE
    ok "logrotate $LOGROTATE_F2B configurado (mantiene 640 root:adm tras rotar)"
  else
    ok "logrotate $LOGROTATE_F2B ya correcto"
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# PASO 11: SELinux (solo si Enforcing)
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="selinux_setup"
step "11/${TOTAL_STEPS} — SELinux (auto-config si Enforcing)"
if [ "$SKIP_SELINUX" = "1" ]; then
  info "SELinux setup saltado (--skip-selinux)"
elif [ "$DRY_RUN" = "1" ]; then
  if command -v getenforce >/dev/null 2>&1; then
    SE_MODE="$(getenforce 2>/dev/null || echo Disabled)"
    info "(dry-run) SELinux: $SE_MODE"
    [ "$SE_MODE" = "Enforcing" ] && info "(dry-run) configuraría contexto var_log_t para $F2B_LOG + bin_t para $INSTALL_DIR/bin/"
  else
    info "(dry-run) SELinux no instalado"
  fi
else
  check_selinux_context
fi

# ═══════════════════════════════════════════════════════════════════════════
# PASO 12: systemd unit + sudoers + jail almc-blocklist
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="systemd_install"
step "12/${TOTAL_STEPS} — systemd unit + sudoers + jail dedicado"

# Detección de container LXC/Docker (sin CAP_SYS_ADMIN → mount namespacing falla)
VIRT_TYPE="unknown"
if command -v systemd-detect-virt >/dev/null 2>&1; then
  VIRT_TYPE="$(systemd-detect-virt 2>/dev/null || echo unknown)"
fi
NEEDS_NS_DISABLE=0
case "$VIRT_TYPE" in
  lxc|lxc-libvirt)
    # LXC unprivileged bloquea mount namespacing
    NEEDS_NS_DISABLE=1
    ;;
  docker|podman)
    # Docker/Podman containers tampoco soportan mount ns sin --privileged
    NEEDS_NS_DISABLE=1
    ;;
  openvz|systemd-nspawn)
    # OpenVZ y nspawn también limitan namespacing
    NEEDS_NS_DISABLE=1
    ;;
esac
# Fallback: si systemd-detect-virt no existe, mirar /proc/1/cgroup y /.dockerenv
if [ "$NEEDS_NS_DISABLE" = "0" ]; then
  if [ -r /proc/1/cgroup ] && grep -qiE 'lxc|/lxc/' /proc/1/cgroup 2>/dev/null; then
    NEEDS_NS_DISABLE=1; VIRT_TYPE="lxc"
  elif [ -f /.dockerenv ]; then
    NEEDS_NS_DISABLE=1; VIRT_TYPE="docker"
  elif [ -r /proc/1/cgroup ] && grep -qE 'docker|/kubepods/' /proc/1/cgroup 2>/dev/null; then
    NEEDS_NS_DISABLE=1; VIRT_TYPE="docker"
  fi
fi
# Mantener compat con código que usa IS_LXC
IS_LXC=$NEEDS_NS_DISABLE

if [ "$DRY_RUN" = "1" ]; then
  info "(dry-run) virt detectado: ${VIRT_TYPE}${IS_LXC:+ → patch service sin mount namespacing}"
  info "(dry-run) instalaría:"
  info "  · /etc/systemd/system/almc-shield.service"
  info "  · /etc/sudoers.d/almc-shield (validado con visudo)"
  info "  · /etc/fail2ban/jail.d/almc-blocklist.conf"
  info "  · /etc/fail2ban/filter.d/almc-blocklist.conf"
else
  # systemd
  if [ -d "/run/systemd/system" ]; then
    cp "$INSTALL_DIR/share/systemd/almc-shield.service" /etc/systemd/system/almc-shield.service
    if [ "$IS_LXC" = "1" ]; then
      # En LXC sin privilegios, las directivas que requieren mount namespacing
      # rompen el arranque (status 226/NAMESPACE). Las comentamos.
      sed -i '/^ProtectSystem=/s/^/#LXC-DISABLED: /' /etc/systemd/system/almc-shield.service
      sed -i '/^ProtectHome=/s/^/#LXC-DISABLED: /' /etc/systemd/system/almc-shield.service
      sed -i '/^PrivateTmp=/s/^/#LXC-DISABLED: /' /etc/systemd/system/almc-shield.service
      sed -i '/^ReadWritePaths=/s/^/#LXC-DISABLED: /' /etc/systemd/system/almc-shield.service
      info "LXC detectado (${VIRT_TYPE}) → 4 directivas de mount namespacing deshabilitadas"
      report_event "warn" "systemd_install" "LXC detectado: ProtectSystem/Home + PrivateTmp deshabilitados" "lxc_no_namespace"
    fi
    systemctl daemon-reload
    systemctl enable almc-shield.service >/dev/null 2>&1 || true
    ok "Unit systemd instalada y habilitada (virt=${VIRT_TYPE})"
  else
    warn "No hay /run/systemd/system — no se instala el servicio.\n   Tendrás que arrancar el agente manualmente o configurar tu init."
  fi

  # sudoers
  if [ -d "/etc/sudoers.d" ]; then
    # Verificar que /etc/sudoers carga /etc/sudoers.d/ (default en mayoría
    # distros, pero algunas custom — alpine minimal, BeagleBoneBlack — no).
    if ! grep -qE "^[[:space:]]*#?includedir[[:space:]]+/etc/sudoers\.d" /etc/sudoers 2>/dev/null \
       && ! grep -qE "^[[:space:]]*@includedir[[:space:]]+/etc/sudoers\.d" /etc/sudoers 2>/dev/null; then
      warn "/etc/sudoers NO incluye /etc/sudoers.d/ — nuestro sudoers/almc-shield no surtirá efecto"
      warn "Añade manualmente esta línea al FINAL de /etc/sudoers:"
      warn "  @includedir /etc/sudoers.d"
      report_event "warn" "sudoers_includedir_missing" "/etc/sudoers no carga /etc/sudoers.d" "sudoers_no_include"
    fi
    cp "$INSTALL_DIR/share/sudoers.d/almc-shield" /etc/sudoers.d/almc-shield
    chmod 440 /etc/sudoers.d/almc-shield
    if command -v visudo >/dev/null 2>&1; then
      if ! visudo -cf /etc/sudoers.d/almc-shield >/dev/null 2>&1; then
        rm -f /etc/sudoers.d/almc-shield
        fatal "El archivo sudoers tiene sintaxis inválida. Eliminado para no romper sudo."
      fi
    fi
    ok "sudoers instalado (limitado a 4 subcomandos de fail2ban-client)"
  fi

  # jail almc-blocklist
  if command -v fail2ban-client >/dev/null 2>&1 && [ -d "/etc/fail2ban/jail.d" ]; then
    cp "$INSTALL_DIR/share/fail2ban/jail.d/almc-blocklist.conf" /etc/fail2ban/jail.d/
    cp "$INSTALL_DIR/share/fail2ban/filter.d/almc-blocklist.conf" /etc/fail2ban/filter.d/
    ok "Jail + filter almc-blocklist instalados en /etc/fail2ban/"

    if [ "$SKIP_JAIL_RELOAD" != "1" ]; then
      if systemctl is-active --quiet fail2ban 2>/dev/null; then
        fail2ban-client reload >/dev/null 2>&1 \
          && ok "fail2ban recargado (jail almc-blocklist activo)" \
          || warn "fail2ban no respondió al reload. Verifica: fail2ban-client status almc-blocklist"
      else
        info "fail2ban no está corriendo. Arráncalo: systemctl start fail2ban"
      fi
    else
      info "Reload de fail2ban saltado (--skip-jail-reload)"
    fi
  fi
  report_event "step" "systemd_install" "systemd unit + sudoers + jail almc-blocklist instalados"
fi

# ═══════════════════════════════════════════════════════════════════════════
# PASO 13: Arranque del servicio
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="service_start"
step "13/${TOTAL_STEPS} — Arrancando el agente"
if [ "$DRY_RUN" = "1" ]; then
  info "(dry-run) ejecutaría: systemctl restart almc-shield.service"
elif [ -d "/run/systemd/system" ]; then
  systemctl restart almc-shield.service
  sleep 3
  if systemctl is-active --quiet almc-shield.service; then
    ok "almc-shield.service ${GREEN}active${RESET}"
    report_event "step" "service_start" "Servicio almc-shield arrancado"
  else
    warn "El servicio no arrancó correctamente. Diagnóstico:"
    journalctl -u almc-shield --no-pager -n 30 || true
    fatal "Servicio almc-shield falló al arrancar. Revisa los logs anteriores." "service_start_failed"
  fi
else
  info "No hay systemd — arranca el agente manualmente:"
  info "  /opt/almc-shield/bin/almc-shield --config /etc/almc-shield/config.ini"
  report_event "warn" "service_start" "No hay systemd disponible, arranque manual requerido" "no_systemd"
fi

# ═══════════════════════════════════════════════════════════════════════════
# PASO 14: Health check post-install + auto-update opcional
# ═══════════════════════════════════════════════════════════════════════════
CURRENT_STEP="health_check"
step "14/${TOTAL_STEPS} — Health check E2E + auto-update opcional"
if [ "$DRY_RUN" = "1" ]; then
  info "(dry-run) verificaría agente activo + POST heartbeat backend"
  [ "$AUTO_UPDATE" = "1" ] && info "(dry-run) instalaría systemd timer almc-shield-update.timer"
else
  health_check_e2e
  [ "$AUTO_UPDATE" = "1" ] && setup_auto_update_timer
fi

# ── Reportar success al final ────────────────────────────────────────────────
[ "$DRY_RUN" != "1" ] && report_event "success" "install_complete" "Instalación completa OK"
INSTALL_FINISHED_OK=1   # Marca para el trap EXIT: NO mostrar mensaje abort

# ── Resumen final ────────────────────────────────────────────────────────────
if [ "$DRY_RUN" = "1" ]; then
  cat <<EOF

${BOLD}${YELLOW}╔══════════════════════════════════════════════════════════════╗${RESET}
${BOLD}${YELLOW}║${RESET}   ${BOLD}✓ DRY-RUN completado — el sistema NO ha cambiado${RESET}        ${BOLD}${YELLOW}║${RESET}
${BOLD}${YELLOW}╚══════════════════════════════════════════════════════════════╝${RESET}

  Para instalar de verdad: ${BOLD}elimina el flag --dry-run${RESET} y reejecuta.
  Cualquier fatal en este modo indica un blocker real que hay que resolver.

EOF
else
  cat <<EOF

${BOLD}${GREEN}╔══════════════════════════════════════════════════════════════╗${RESET}
${BOLD}${GREEN}║${RESET}   ${BOLD}✓ ALMC Abuse Shield agent v${AGENT_VERSION} instalado${RESET}                 ${BOLD}${GREEN}║${RESET}
${BOLD}${GREEN}╚══════════════════════════════════════════════════════════════╝${RESET}

  ${BOLD}Estado:${RESET}       systemctl status almc-shield
  ${BOLD}Logs:${RESET}         journalctl -u almc-shield -f
  ${BOLD}Logs file:${RESET}    tail -f /var/log/almc-shield/agent.log
  ${BOLD}Config:${RESET}       /etc/almc-shield/config.ini
  ${BOLD}Panel:${RESET}        https://almc.es/es/dash/abuse-shield
EOF
  [ -n "$DETECTED_PANEL" ] && echo "  ${BOLD}${YELLOW}Aviso:${RESET}        Panel ${DETECTED_PANEL} detectado — ver nota arriba."
  [ "$AUTO_UPDATE" = "1" ] && echo "  ${BOLD}Auto-update:${RESET}  systemctl status almc-shield-update.timer"
  cat <<EOF

  El primer heartbeat llegará al panel en ~60 segundos.
  Cada ban de fail2ban se reportará en tiempo real (batches cada 30s).

  ${DIM}Para desinstalar: curl -fsSL https://almc.es/abuse-shield/uninstall.sh | bash${RESET}

EOF
fi
