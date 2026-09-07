#!/bin/bash
# ============================================
# IMAP Checker VPS Setup Script
# Ubuntu 24.04 LTS
# ============================================

set -e

APP_NAME="imap-checker"
APP_DIR="/opt/$APP_NAME"
APP_USER="imapchecker"
VENV_DIR="$APP_DIR/venv"
PORT=5000

echo "======================================"
echo " IMAP Checker — VPS Setup (Ubuntu 24)"
echo "======================================"

# 1. Update system
echo "[1/7] Updating system packages..."
sudo apt update && sudo apt upgrade -y

# 2. Install Python & dependencies
echo "[2/7] Installing Python 3 & pip..."
sudo apt install -y python3 python3-pip python3-venv nginx

# 3. Create app user (no password, no login shell)
echo "[3/7] Creating app user..."
if ! id "$APP_USER" &>/dev/null; then
    sudo useradd -r -s /bin/false -m -d /home/$APP_USER $APP_USER
    echo "  User '$APP_USER' created"
else
    echo "  User '$APP_USER' already exists"
fi

# 4. Copy app files
echo "[4/7] Setting up application directory..."
sudo mkdir -p $APP_DIR
sudo cp -r ./* $APP_DIR/
sudo rm -f $APP_DIR/setup.sh $APP_DIR/nginx.conf $APP_DIR/imap-checker.service

# 5. Create virtual environment & install deps
echo "[5/7] Creating Python virtual environment..."
sudo python3 -m venv $VENV_DIR
sudo $VENV_DIR/bin/pip install --upgrade pip
sudo $VENV_DIR/bin/pip install -r $APP_DIR/requirements.txt

# Create jobs directory
sudo mkdir -p $APP_DIR/jobs

# Seed empty IMAP config DB so first run never crashes with
# "[Errno 2] No such file or directory: imap_config.json".
# The real data is gitignored, so a fresh clone has none — create both the
# app-dir file (fallback) and the central /opt/pmj/imap DB (production path).
echo "  Seeding empty IMAP config DB..."
CENTRAL_IMAP_DIR="/opt/pmj/imap"
sudo mkdir -p "$CENTRAL_IMAP_DIR"
for f in "$APP_DIR/imap_config.json" "$APP_DIR/imap_success.json" \
         "$CENTRAL_IMAP_DIR/imap_config.json" "$CENTRAL_IMAP_DIR/imap_success.json"; do
    if [ ! -f "$f" ]; then
        printf '{}\n' | sudo tee "$f" >/dev/null
    fi
done
sudo chmod 755 /opt/pmj "$CENTRAL_IMAP_DIR"
sudo chown -R $APP_USER:$APP_USER "$CENTRAL_IMAP_DIR"

sudo chown -R $APP_USER:$APP_USER $APP_DIR

# Permission fix so nginx (www-data) can read static/ + templates/.
# Default useradd creates home with mode 750 → nginx can't traverse $APP_DIR.
# Make the path traversable and static/templates world-readable.
echo "  Fixing permissions for nginx static file access..."
sudo chmod 755 /opt $APP_DIR
sudo chmod -R a+rX $APP_DIR/static $APP_DIR/templates
# Add www-data to imapchecker group as a defense-in-depth fallback
# in case any per-file permission gets restricted later.
sudo usermod -a -G $APP_USER www-data 2>/dev/null || true

# 6. Install systemd service
echo "[6/7] Installing systemd service..."
sudo cp imap-checker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable $APP_NAME
sudo systemctl start $APP_NAME
echo "  Service installed and started"

# 7. Install nginx config
echo "[7/7] Configuring nginx reverse proxy..."
sudo cp nginx.conf /etc/nginx/sites-available/$APP_NAME
sudo ln -sf /etc/nginx/sites-available/$APP_NAME /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
echo "  Nginx configured"

# Open firewall
echo ""
echo "Opening firewall ports..."
sudo ufw allow 'Nginx Full' 2>/dev/null || true
sudo ufw allow OpenSSH 2>/dev/null || true

# Mount path for the real tool. Defaults to "sbb"; override with .secret_path
# in the app dir if you want a different segment.
SECRET_PATH="sbb"
if [ -f "$APP_DIR/.secret_path" ]; then
    _p="$(sudo cat "$APP_DIR/.secret_path" 2>/dev/null | tr -d '[:space:]')"
    [ -n "$_p" ] && SECRET_PATH="$_p"
fi

IP="$(hostname -I | awk '{print $1}')"

echo ""
echo "======================================"
echo " ✅ Setup Complete!"
echo "======================================"
echo ""
echo " PUBLIC homepage : http://$IP/            (hello world — safe to share)"
echo " PRIVATE tool    : http://$IP/$SECRET_PATH/"
echo ""
echo " Useful commands:"
echo "   sudo systemctl status $APP_NAME    # Check status"
echo "   sudo systemctl restart $APP_NAME   # Restart app"
echo "   sudo systemctl stop $APP_NAME      # Stop app"
echo "   sudo journalctl -u $APP_NAME -f    # View logs"
echo ""
echo " Default access code: sbb  (change it after first login)"
echo "======================================"
