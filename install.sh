#!/bin/bash
# install.sh — Install ALMC Abuse Shield agent.
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
# Flags útiles:
#   --skip-fail2ban-install   No instala fail2ban automáticamente si falta
#   --skip-api-test           Saltar el test de conectividad con el API
#   --skip-jail-reload        No recargar fail2ban tras copiar el jail
#   --force-permissions       Forzar fix de permisos en /var/log/fail2ban.log
#
# Idempotent. Compatible con Debian / Ubuntu / RHEL / Rocky / Alma / Alpine,
# incluido en containers LXC / Docker minimales sin sudo.

set -euo pipefail

# ── Configuración fija ───────────────────────────────────────────────────────
INSTALL_DIR="/opt/almc-shield"
CONFIG_DIR="/etc/almc-shield"
STATE_DIR="/var/lib/almc-shield"
LOG_DIR="/var/log/almc-shield"
USER="almc-shield"
GROUP="almc-shield"
SERVICE_NAME="almc-shield"
AGENT_TARBALL_URL="https://almc.es/abuse-shield-agent-1.0.0.tar.gz"
AGENT_VERSION="1.0.0"

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
fatal()   {
    local msg="$1"; local err_code="${2:-fatal}"
    report_event "error" "${CURRENT_STEP:-unknown}" "$msg" "$err_code"
    echo ""; echo "${BOLD}${RED}✗ ERROR:${RESET} $msg" >&2; exit 1;
}
info()    { echo "  ${DIM}·${RESET} $1"; }

# ── Telemetría hacia el servidor central (fire-and-forget) ───────────────────
# Reporta cada paso al endpoint POST /api/v1/abuse/install-event para que el
# cliente vea el progreso en su panel /dash/abuse-shield/install. Si el endpoint
# falla, el install continúa (NO bloqueante).
CURRENT_STEP="boot"
report_event() {
    # Args: <event> <step_name> [message] [error_code]
    local event="$1" step="$2" message="${3:-}" err_code="${4:-}"
    [ -z "$API_KEY" ] && return 0   # No tenemos key todavía
    [ -z "$API_URL" ] && return 0
    local hostname; hostname=$(hostname 2>/dev/null || echo "unknown")
    local os_info; os_info=$(uname -s -r 2>/dev/null || echo "unknown")
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

# ── Args + env vars ──────────────────────────────────────────────────────────
API_KEY="${ABUSE_SHIELD_API_KEY:-}"
API_URL="${ABUSE_SHIELD_API_URL:-https://almc.es/api/v1/abuse}"
SKIP_FAIL2BAN_INSTALL="${ABUSE_SHIELD_SKIP_FAIL2BAN_INSTALL:-0}"
SKIP_API_TEST="${ABUSE_SHIELD_SKIP_API_TEST:-0}"
SKIP_JAIL_RELOAD="${ABUSE_SHIELD_SKIP_JAIL_RELOAD:-0}"
FORCE_PERMISSIONS="${ABUSE_SHIELD_FORCE_PERMISSIONS:-0}"

for arg in "$@"; do
  case "$arg" in
    --api-key=*)             API_KEY="${arg#*=}" ;;
    --api-url=*)             API_URL="${arg#*=}" ;;
    --skip-fail2ban-install) SKIP_FAIL2BAN_INSTALL=1 ;;
    --skip-api-test)         SKIP_API_TEST=1 ;;
    --skip-jail-reload)      SKIP_JAIL_RELOAD=1 ;;
    --force-permissions)     FORCE_PERMISSIONS=1 ;;
    -h|--help)
      cat <<'USAGE'
ALMC Abuse Shield agent installer.

REQUIRED:
  --api-key=KEY     Tu API key (formato ab_live_*)
                    o env var ABUSE_SHIELD_API_KEY

OPTIONAL:
  --api-url=URL              Override del endpoint
                             (env ABUSE_SHIELD_API_URL, default almc.es/api/v1/abuse)
  --skip-fail2ban-install    No instalar fail2ban automáticamente si falta
  --skip-api-test            No verificar conectividad con el API antes de arrancar
  --skip-jail-reload         No recargar fail2ban tras copiar la config del jail
  --force-permissions        Forzar fix de permisos en /var/log/fail2ban.log

EXAMPLES:
  install.sh --api-key=ab_live_XXX
  curl -fsSL https://almc.es/abuse-shield/install.sh | bash -s -- --api-key=ab_live_XXX
  curl -fsSL https://almc.es/abuse-shield/install.sh | ABUSE_SHIELD_API_KEY=ab_live_XXX bash

Obtén tu API key en https://almc.es/es/dash/abuse-shield/settings
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

# ── Banner inicial ───────────────────────────────────────────────────────────
cat <<EOF

${BOLD}${BLUE}╔══════════════════════════════════════════════════════════════╗${RESET}
${BOLD}${BLUE}║${RESET}   ${BOLD}ALMC Abuse Shield Agent${RESET} — installer v${AGENT_VERSION}              ${BOLD}${BLUE}║${RESET}
${BOLD}${BLUE}╚══════════════════════════════════════════════════════════════╝${RESET}

  API URL:  ${API_URL}
  API key:  ${API_KEY:0:14}...${API_KEY: -4}
  Host:     $(hostname)
  OS:       $(uname -s -r)

EOF

# ── Reportar inicio del install ──────────────────────────────────────────────
report_event "start" "boot" "Install script iniciado en $(hostname)"

# ── PASO 1: Detección de distro ──────────────────────────────────────────────
CURRENT_STEP="distro_detect"
step "1/11 — Detectando sistema"
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
elif command -v apk >/dev/null 2>&1; then
  DISTRO="alpine"
  PKG_INSTALL="apk add --no-cache --quiet"
  PKG_UPDATE=":"
else
  fatal "Distro no soportada (no se encontró apt/dnf/yum/apk)."
fi
ok "Distro detectada: ${BOLD}${DISTRO}${RESET}"
report_event "step" "distro_detect" "Distro: $DISTRO"

# ── PASO 2: Pre-flight de conectividad ──────────────────────────────────────
CURRENT_STEP="api_test"
step "2/11 — Validando conectividad con el servidor central"
if ! command -v curl >/dev/null 2>&1; then
  warn "curl no está instalado, instalándolo..."
  $PKG_UPDATE >/dev/null 2>&1 || true
  $PKG_INSTALL curl >/dev/null 2>&1 || fatal "No se pudo instalar curl"
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

# ── PASO 3: Python 3.8+ + venv ───────────────────────────────────────────────
CURRENT_STEP="python_check"
step "3/11 — Python 3.8+ con módulo venv"
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
  $PKG_UPDATE >/dev/null 2>&1 || true
  case "$DISTRO" in
    debian) $PKG_INSTALL python3 python3-venv >/dev/null 2>&1 ;;
    rhel)   $PKG_INSTALL python3 python3-pip >/dev/null 2>&1 ;;
    alpine) $PKG_INSTALL python3 py3-pip >/dev/null 2>&1 ;;
  esac
  PYTHON_BIN="python3"
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  fatal "No se pudo instalar Python 3.8+. Instálalo manualmente y reintenta." "python_missing"
fi
PYTHON_VERSION_STR="$($PYTHON_BIN --version 2>&1)"
ok "$PYTHON_VERSION_STR"
report_event "step" "python_check" "$PYTHON_VERSION_STR"

# Validar que ensurepip está disponible (Debian/Ubuntu separan python3 vs python3-venv)
# El error típico: "ensurepip is not available. On Debian/Ubuntu systems, you need to install the python3-venv package"
if ! "$PYTHON_BIN" -c 'import ensurepip' >/dev/null 2>&1; then
  info "ensurepip no disponible — instalando python3-venv..."
  case "$DISTRO" in
    debian)
      # En Debian/Ubuntu, el paquete puede llamarse python3-venv o python3.X-venv
      PYTHON_MINOR=$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
      $PKG_UPDATE >/dev/null 2>&1 || true
      $PKG_INSTALL python3-venv "python${PYTHON_MINOR}-venv" >/dev/null 2>&1 \
        || $PKG_INSTALL python3-venv >/dev/null 2>&1 \
        || fatal "No se pudo instalar python3-venv. Ejecuta manualmente: apt install python3-venv"
      ;;
    rhel)
      $PKG_INSTALL python3-pip >/dev/null 2>&1 || true
      ;;
    alpine)
      $PKG_INSTALL py3-virtualenv >/dev/null 2>&1 || true
      ;;
  esac
  # Re-verificar
  if ! "$PYTHON_BIN" -c 'import ensurepip' >/dev/null 2>&1; then
    fatal "ensurepip sigue sin estar disponible. Instala manualmente python3-venv y reintenta." "ensurepip_missing"
  fi
  ok "python3-venv instalado"
  report_event "step" "venv_module" "python3-venv instalado automáticamente"
else
  ok "venv listo"
  report_event "step" "venv_module" "venv ya disponible"
fi

# ── PASO 4: Usuario sistema ──────────────────────────────────────────────────
CURRENT_STEP="user_create"
step "4/11 — Usuario sistema $USER"
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

# ── PASO 5: Directorios ──────────────────────────────────────────────────────
CURRENT_STEP="dirs_create"
step "5/11 — Creando directorios"
mkdir -p "$INSTALL_DIR/bin" "$INSTALL_DIR/lib" "$INSTALL_DIR/share"
mkdir -p "$CONFIG_DIR" "$STATE_DIR" "$LOG_DIR"
chown root:root "$INSTALL_DIR" "$CONFIG_DIR"
chmod 755 "$INSTALL_DIR" "$CONFIG_DIR"
chown "$USER:$GROUP" "$STATE_DIR" "$LOG_DIR"
chmod 700 "$STATE_DIR"
chmod 750 "$LOG_DIR"
ok "Directorios listos: $INSTALL_DIR, $CONFIG_DIR, $STATE_DIR, $LOG_DIR"
report_event "step" "dirs_create" "Directorios creados"

# ── PASO 6: Descarga o copia del paquete ─────────────────────────────────────
CURRENT_STEP="package_download"
step "6/11 — Obteniendo el paquete del agente"
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
fi

if [ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/almc_shield" ]; then
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
report_event "step" "package_download" "Paquete descargado y extraído"

# ── PASO 7: venv + dependencias ──────────────────────────────────────────────
CURRENT_STEP="venv_setup"
step "7/11 — Python venv + dependencias"
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

# ── PASO 8: config.ini ───────────────────────────────────────────────────────
CURRENT_STEP="config_generate"
step "8/11 — Generando configuración"
if [ ! -f "$CONFIG_DIR/config.ini" ]; then
  cp "$SCRIPT_DIR/config.ini.example" "$CONFIG_DIR/config.ini"
  # Sustituciones (escapar / para sed)
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

# ── PASO 9: fail2ban (instalación + permisos del log) ────────────────────────
CURRENT_STEP="fail2ban_setup"
step "9/11 — fail2ban (instalación + permisos)"
if ! command -v fail2ban-client >/dev/null 2>&1; then
  if [ "$SKIP_FAIL2BAN_INSTALL" = "1" ]; then
    warn "fail2ban-client no instalado y --skip-fail2ban-install activo.\n   El agente arrancará pero no podrá reportar nada hasta que instales fail2ban."
  else
    info "fail2ban no instalado, instalándolo..."
    $PKG_UPDATE >/dev/null 2>&1 || true
    case "$DISTRO" in
      debian)   $PKG_INSTALL fail2ban >/dev/null 2>&1 ;;
      rhel)     $PKG_INSTALL fail2ban >/dev/null 2>&1 || $PKG_INSTALL epel-release >/dev/null 2>&1 && $PKG_INSTALL fail2ban >/dev/null 2>&1 ;;
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
  F2B_VER=$(fail2ban-client --version 2>&1 | head -1)
  ok "fail2ban-client ya instalado: $F2B_VER"
  report_event "step" "fail2ban_setup" "fail2ban ya instalado: $F2B_VER"
fi

# Permisos /var/log/fail2ban.log — el agente necesita read access
F2B_LOG="/var/log/fail2ban.log"
if [ -f "$F2B_LOG" ]; then
  # Comprobar si el usuario almc-shield puede leerlo
  if ! sudo -u "$USER" -n test -r "$F2B_LOG" 2>/dev/null; then
    if [ "$FORCE_PERMISSIONS" = "1" ] || true; then
      # Forzamos: chgrp adm + chmod 640
      chgrp adm "$F2B_LOG" 2>/dev/null || true
      chmod 640 "$F2B_LOG" 2>/dev/null || true
      ok "Permisos $F2B_LOG ajustados a 640 root:adm"
    fi
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

# ── PASO 10: systemd unit + sudoers + jail almc-blocklist ────────────────────
CURRENT_STEP="systemd_install"
step "10/11 — systemd unit + sudoers + jail dedicado"

# systemd
if [ -d "/run/systemd/system" ]; then
  cp "$INSTALL_DIR/share/systemd/almc-shield.service" /etc/systemd/system/almc-shield.service
  systemctl daemon-reload
  systemctl enable almc-shield.service >/dev/null 2>&1 || true
  ok "Unit systemd instalada y habilitada"
else
  warn "No hay /run/systemd/system — no se instala el servicio.\n   Tendrás que arrancar el agente manualmente o configurar tu init."
fi

# sudoers
if [ -d "/etc/sudoers.d" ]; then
  cp "$INSTALL_DIR/share/sudoers.d/almc-shield" /etc/sudoers.d/almc-shield
  chmod 440 /etc/sudoers.d/almc-shield
  # visudo -c para validar la sintaxis (si visudo disponible)
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

# ── PASO 11: Arranque del servicio ──────────────────────────────────────────
CURRENT_STEP="service_start"
step "11/11 — Arrancando el agente"
if [ -d "/run/systemd/system" ]; then
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

# ── Reportar success al final ────────────────────────────────────────────────
report_event "success" "install_complete" "Instalación completa OK"

# ── Resumen final ────────────────────────────────────────────────────────────
cat <<EOF

${BOLD}${GREEN}╔══════════════════════════════════════════════════════════════╗${RESET}
${BOLD}${GREEN}║${RESET}   ${BOLD}✓ ALMC Abuse Shield agent instalado${RESET}                       ${BOLD}${GREEN}║${RESET}
${BOLD}${GREEN}╚══════════════════════════════════════════════════════════════╝${RESET}

  ${BOLD}Estado:${RESET}       systemctl status almc-shield
  ${BOLD}Logs:${RESET}         journalctl -u almc-shield -f
  ${BOLD}Logs file:${RESET}    tail -f /var/log/almc-shield/agent.log
  ${BOLD}Config:${RESET}       /etc/almc-shield/config.ini
  ${BOLD}Panel:${RESET}        https://almc.es/es/dash/abuse-shield

  El primer heartbeat llegará al panel en ~60 segundos.
  Cada ban de fail2ban se reportará en tiempo real (batches cada 30s).

  ${DIM}Para desinstalar: curl -fsSL https://almc.es/abuse-shield/uninstall.sh | bash${RESET}

EOF
