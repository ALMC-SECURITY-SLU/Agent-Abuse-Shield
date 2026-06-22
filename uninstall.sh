#!/bin/bash
# uninstall.sh — Remove ALMC Abuse Shield agent.
set -euo pipefail

if [ "$EUID" -ne 0 ]; then echo "must run as root"; exit 1; fi

echo "=== Stopping service ==="
systemctl stop almc-shield 2>/dev/null || true
systemctl disable almc-shield 2>/dev/null || true
rm -f /etc/systemd/system/almc-shield.service
systemctl daemon-reload 2>/dev/null || true

echo "=== Removing files ==="
rm -rf /opt/almc-shield /etc/almc-shield /var/lib/almc-shield /var/log/almc-shield
rm -f /usr/local/bin/almc-shield /usr/local/bin/shield /etc/sudoers.d/almc-shield
rm -f /etc/fail2ban/jail.d/almc-blocklist.conf /etc/fail2ban/filter.d/almc-blocklist.conf

echo "=== Removing user ==="
userdel almc-shield 2>/dev/null || deluser almc-shield 2>/dev/null || true

echo "OK done"
