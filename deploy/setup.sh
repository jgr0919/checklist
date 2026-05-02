#!/bin/bash
# First-time setup script for a fresh Lightsail Ubuntu instance.
# Run once as the ubuntu user after SSH-ing into the server:
#   bash setup.sh

set -e

REPO_URL="https://github.com/jgr0919/checklist.git"
APP_DIR="/home/ubuntu/checklist"
DB_NAME="checklist"
DB_USER="checklist"

echo "=== 1. System packages ==="
sudo apt-get update -q
sudo apt-get install -y -q \
    python3.11 python3.11-venv python3.11-dev \
    postgresql postgresql-contrib \
    nginx \
    git \
    build-essential libpq-dev

echo "=== 2. PostgreSQL setup ==="
sudo systemctl enable postgresql
sudo systemctl start postgresql

# Generate a random DB password and store it for later
DB_PASS=$(openssl rand -hex 20)

sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || \
    sudo -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';"
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" 2>/dev/null || true

echo "=== 3. Clone repo ==="
if [ -d "$APP_DIR" ]; then
    echo "Directory already exists — pulling latest."
    git -C "$APP_DIR" pull origin main
else
    git clone "$REPO_URL" "$APP_DIR"
fi

echo "=== 4. Python virtualenv & dependencies ==="
python3.11 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo "=== 5. .env file ==="
SECRET_KEY=$(openssl rand -hex 32)

cat > "$APP_DIR/.env" <<EOF
SECRET_KEY=$SECRET_KEY
DB_USER=$DB_USER
DB_PASS=$DB_PASS
DB_HOST=localhost
DB_PORT=5432
DB_NAME=$DB_NAME

# Optional: set S3_BUCKET to enable S3 image storage
# S3_BUCKET=
# AWS_DEFAULT_REGION=us-east-2

# Optional: Planning Center Online login
# PCO_APP_ID=
# PCO_SECRET=
EOF

chmod 600 "$APP_DIR/.env"

echo "=== 6. Log directory ==="
sudo mkdir -p /var/log/checklist
sudo chown ubuntu:ubuntu /var/log/checklist

echo "=== 7. Systemd service ==="
sudo cp "$APP_DIR/deploy/checklist.service" /etc/systemd/system/checklist.service
sudo systemctl daemon-reload
sudo systemctl enable checklist
sudo systemctl start checklist

echo "=== 8. Nginx ==="
sudo cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/checklist
sudo ln -sf /etc/nginx/sites-available/checklist /etc/nginx/sites-enabled/checklist
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx

echo ""
echo "=== Setup complete ==="
echo ""
echo "App is running at http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo '<your-lightsail-ip>')"
echo ""
echo "Default admin login: admin / Admin@1234"
echo "Change this immediately after first login."
echo ""
echo "DB password saved to: $APP_DIR/.env"
