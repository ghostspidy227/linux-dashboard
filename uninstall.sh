#!/usr/bin/env bash
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
log()  { echo -e "${CYAN}[*]${NC} $1"; }
ok()   { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}[x]${NC} This script must be run as root."
    exit 1
fi

INSTALL_DIR="/opt/linux-dashboard"
SERVICE_NAME="linux-dashboard"

if [ ! -d "$INSTALL_DIR" ]; then
    warn "$INSTALL_DIR not found — nothing to uninstall."
    exit 0
fi

systemctl stop "$SERVICE_NAME" 2>/dev/null || true
systemctl disable "$SERVICE_NAME" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload

BACKUP="/root/linux-dashboard-data-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
if [ -d "$INSTALL_DIR/data" ]; then
    tar -czf "$BACKUP" -C "$INSTALL_DIR" data
    ok "Data backed up to $BACKUP (contains config, audit/IP logs, metrics history, AI threads)."
fi

rm -rf "$INSTALL_DIR"
ok "Linux Dashboard removed."
echo ""
echo "  Delete the backup too?  rm $BACKUP"
