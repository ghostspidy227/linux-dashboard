#!/usr/bin/env bash
set -e

# ── Colors ──────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'

log()  { echo -e "${CYAN}[*]${NC} $1"; }
ok()   { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[x]${NC} $1"; }

# ── Check root ─────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    err "This script must be run as root."
    exit 1
fi

INSTALL_DIR="/opt/linux-dashboard"
SERVICE_NAME="linux-dashboard"
CONFIG_FILE="$INSTALL_DIR/data/config.json"

# ── Detect distro ──────────────────────────────────────
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        DISTRO_ID="$ID"
        DISTRO_NAME="$PRETTY_NAME"
    else
        DISTRO_ID="unknown"
        DISTRO_NAME="unknown"
    fi

    # Determine package manager
    if command -v apt-get &>/dev/null; then
        PKG_MGR="apt"
    elif command -v dnf &>/dev/null; then
        PKG_MGR="dnf"
    elif command -v pacman &>/dev/null; then
        PKG_MGR="pacman"
    else
        err "No supported package manager found (apt, dnf, or pacman)."
        err "Please install dependencies manually: python3, pip, whois, dnsutils/bind-utils, iproute2, nmap"
        exit 1
    fi

    log "Detected: $DISTRO_NAME"
    log "Package manager: $PKG_MGR"
}

# ── Install system deps ────────────────────────────────
install_system_deps() {
    log "Installing system dependencies..."

    case $PKG_MGR in
        apt)
            if [ ! -f "/var/cache/apt/pkgcache.bin" ] || [ "$(( $(date +%s) - $(stat -c %Y /var/cache/apt/pkgcache.bin 2>/dev/null || echo 0) ))" -gt 3600 ]; then
                log "Updating apt cache..."
                apt-get update -qq
            fi
            apt-get install -y python3 python3-pip python3-venv whois dnsutils iproute2 nmap
            ;;
        dnf)
            local DNF_CMD="dnf"
            command -v dnf5 &>/dev/null && DNF_CMD="dnf5"
            $DNF_CMD install -y python3 python3-pip whois bind-utils iproute nmap
            ;;
        pacman)
            pacman -S --noconfirm python python-pip whois bind iproute2 nmap
            ;;
    esac

    ok "System dependencies installed."
}

# ── Clone/copy project (preserving existing data/) ─────
install_project() {
    log "Installing project to $INSTALL_DIR..."

    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

    if [ -d "$INSTALL_DIR" ]; then
        warn "$INSTALL_DIR already exists. Upgrading — data/ (config, logs, history) is preserved."
        systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    fi

    # Back up existing data, replace code, restore data
    local BACKUP="/tmp/linux-dashboard-data-backup.$$"
    if [ -d "$INSTALL_DIR/data" ]; then
        mkdir -p "$BACKUP"
        cp -a "$INSTALL_DIR/data/." "$BACKUP/"
    fi

    rm -rf "$INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    cp -r "$SCRIPT_DIR" "$INSTALL_DIR"

    if [ -d "$BACKUP" ]; then
        mkdir -p "$INSTALL_DIR/data"
        cp -a "$BACKUP/." "$INSTALL_DIR/data/"
        rm -rf "$BACKUP"
    fi

    # Code readable, data locked down
    chown -R root:root "$INSTALL_DIR"
    find "$INSTALL_DIR" -type d -exec chmod 755 {} +
    find "$INSTALL_DIR" -type f -exec chmod 644 {} +
    chmod 700 "$INSTALL_DIR/data"
    find "$INSTALL_DIR/data" -type f -exec chmod 600 {} + 2>/dev/null || true

    ok "Project installed to $INSTALL_DIR (data preserved)."
}

# ── Python venv + deps ─────────────────────────────────
setup_python() {
    log "Setting up Python virtual environment..."
    cd "$INSTALL_DIR"

    # A venv copied from a checkout (or left over) is never trustworthy — rebuild it
    rm -rf venv
    python3 -m venv venv
    venv/bin/pip install --upgrade pip -q
    venv/bin/pip install -r requirements.txt -q

    ok "Python dependencies installed."
}

# ── Prompt user ────────────────────────────────────────
prompt_user() {
    log ""
    log "── Configuration ────────────────────────────────"

    # Bind address
    echo ""
    echo "  Where should the dashboard listen?"
    echo "    1) localhost only (127.0.0.1) — access only from this machine"
    echo "    2) LAN (0.0.0.0) — access from any device on your network"
    read -r -p "  Choose [1]: " BIND_CHOICE
    BIND_CHOICE="${BIND_CHOICE:-1}"
    if [ "$BIND_CHOICE" = "2" ]; then
        BIND_ADDR="0.0.0.0"
        echo ""
        warn "Binding to 0.0.0.0 means anyone on your network can access the dashboard."
        warn "For remote access, use a VPN (Tailscale, WireGuard, etc.) — do NOT expose this directly to the internet."
    else
        BIND_ADDR="127.0.0.1"
    fi

    # Port
    read -r -p "  Port [7000]: " PORT
    PORT="${PORT:-7000}"

    # Password
    echo ""
    echo "  Set admin password."
    echo "  Default is 'admin' — leaving it unchanged is insecure."
    read -r -s -p "  Password [leave blank for default 'admin']: " ADMIN_PASS
    echo ""

    if [ -z "$ADMIN_PASS" ]; then
        ADMIN_PASS="admin"
        warn "Using default password 'admin'. Please change it in Settings after login."
    fi

    # Hash via stdin (no shell interpolation, nothing visible in `ps`)
    PASSWORD_HASH=$(printf '%s' "$ADMIN_PASS" | "$INSTALL_DIR/venv/bin/python3" -c \
        'import sys, bcrypt; print(bcrypt.hashpw(sys.stdin.buffer.read(), bcrypt.gensalt()).decode())')

    ok "Configuration collected."
}

# ── Write config ───────────────────────────────────────
write_config() {
    log "Writing config..."
    # Written via python/JSON so the bcrypt hash's $ characters survive intact.
    # On top of an existing install, everything except bind/port/password is
    # preserved (AI keys, sections, username, logs are never touched).
    (cd "$INSTALL_DIR" && BIND_ADDR="$BIND_ADDR" PORT_NUM="$PORT" PASS_HASH="$PASSWORD_HASH" \
        venv/bin/python3 - << 'PYEOF'
import json, os

cfg_path = "data/config.json"
try:
    with open(cfg_path) as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        cfg = {}
except Exception:
    cfg = {}

cfg["bind"] = os.environ["BIND_ADDR"]
cfg["port"] = int(os.environ["PORT_NUM"])

auth = cfg.get("auth") if isinstance(cfg.get("auth"), dict) else {}
auth["username"] = auth.get("username") or "admin"
auth["password_hash"] = os.environ["PASS_HASH"]
cfg["auth"] = auth

sections = cfg.get("sections") if isinstance(cfg.get("sections"), dict) else {}
for key in ("metrics", "network", "services", "processes", "packages", "firewall"):
    sections.setdefault(key, True)
cfg["sections"] = sections

ai = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
for key, default in (
    ("enabled", False), ("provider", "ollama"),
    ("ollama_url", "http://localhost:11434"), ("ollama_model", "llama3"),
    ("openai_key", ""), ("openrouter_key", ""), ("gemini_key", ""),
    ("openai_model", "gpt-4o"),
    ("openrouter_model", "mistralai/mistral-7b-instruct"),
    ("gemini_model", "gemini-1.5-flash"),
    ("custom_url", ""), ("custom_key", ""), ("custom_model", ""),
):
    ai.setdefault(key, default)
cfg["ai"] = ai

with open(cfg_path, "w") as f:
    json.dump(cfg, f, indent=2)
os.chmod(cfg_path, 0o600)
PYEOF
    )
    ok "Config written."
}

# ── Create systemd service ─────────────────────────────
create_service() {
    log "Creating systemd service..."

    cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=Linux Control Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python backend/main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl start "$SERVICE_NAME"

    sleep 2

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        ok "Service started successfully."
    else
        err "Service failed to start. Check: journalctl -u $SERVICE_NAME"
        systemctl status "$SERVICE_NAME" || true
        exit 1
    fi
}

# ── Done ───────────────────────────────────────────────
print_summary() {
    local IP_ADDR
    IP_ADDR=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "YOUR_SERVER_IP")

    echo ""
    echo -e "${GREEN}══════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Linux Dashboard installed successfully!${NC}"
    echo -e "${GREEN}══════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  Dashboard URL:  ${CYAN}http://${BIND_ADDR}:${PORT}${NC}"

    if [ "$BIND_ADDR" = "127.0.0.1" ]; then
        echo "  (localhost only — access from this machine)"
        echo ""
        echo "  To access remotely, change bind to 0.0.0.0 in Settings,"
        echo "  then connect via VPN (Tailscale/WireGuard) to:"
        echo -e "  ${CYAN}http://${IP_ADDR}:${PORT}${NC}"
    fi

    echo ""
    echo -e "  Login:  ${CYAN}admin${NC}"
    if [ "$ADMIN_PASS" = "admin" ]; then
        echo -e "  Password: ${YELLOW}admin (DEFAULT — change in Settings!)${NC}"
    else
        echo -e "  Password: ${GREEN}(custom)${NC}"
    fi
    echo ""
    echo -e "  Manage:  ${CYAN}systemctl {start|stop|restart|status} $SERVICE_NAME${NC}"
    echo -e "  Logs:    ${CYAN}journalctl -u $SERVICE_NAME -f${NC}"
    echo -e "  Remove:  ${CYAN}sudo bash $INSTALL_DIR/uninstall.sh${NC}"
    echo ""
}

# ── Main ───────────────────────────────────────────────
echo ""
echo -e "${CYAN}╔══════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   Linux Control Dashboard Installer ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════╝${NC}"
echo ""

detect_distro
install_system_deps
install_project
setup_python
prompt_user
write_config
create_service
print_summary
