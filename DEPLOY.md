# Deploying to life.appi.ca

Target box (fingerprinted 2026-06-12): EC2 t3.medium, ca-central-1, Ubuntu 24.04, nginx 1.24 —
the shared app server also running trade.lavellehealth.ca (rae.service :8011), intake.lavellehealth.ca
(php-fpm), and books.icona.ca (gunicorn socket + celery + MariaDB + Redis). Baseline posture is
already good: key-only SSH, root login off, ufw default-deny (22/80/443 only), fail2ban, unattended-
upgrades, certbot timer. Port 8501 is free.

**Principles:** additive-only (new user, new unit, new vhost — no shared-config edits); match the
house pattern (per-app system user under /var/www, loopback bind behind nginx); sandbox + memory-cap
the service so it can never starve the production tenants; the app is stateless (no DB, no uploads),
so there is nothing to back up — the repo is the source of truth.

## 0. Repo access (run LOCALLY, pick one)

**A — make the repo public (simplest; public prototype, no secrets):**
```bash
gh repo edit icona-git/lifepath-calculator --visibility public --accept-visibility-change-consequences
```

**B — keep private, read-only deploy key** (generate on server in step 2B, then locally):
```bash
gh repo deploy-key add /path/to/id_ed25519.pub --repo icona-git/lifepath-calculator --title "life.appi.ca"
```

## 1. App user (server)

```bash
sudo apt install -y python3-venv   # likely already present; harmless if so
sudo useradd --system --create-home --home-dir /var/www/lifepath --shell /usr/sbin/nologin lifepath
sudo chmod 750 /var/www/lifepath
```

## 2. Clone + env + engine self-test

**A — public repo:**
```bash
sudo -u lifepath git clone https://github.com/icona-git/lifepath-calculator.git /var/www/lifepath/app
```

**B — private via deploy key:**
```bash
sudo -u lifepath mkdir -p /var/www/lifepath/.ssh && sudo -u lifepath chmod 700 /var/www/lifepath/.ssh
sudo -u lifepath ssh-keygen -t ed25519 -f /var/www/lifepath/.ssh/id_ed25519 -N "" -C "lifepath-deploy@life.appi.ca"
sudo cat /var/www/lifepath/.ssh/id_ed25519.pub   # ← add as read-only deploy key (step 0B)
sudo -u lifepath bash -c 'ssh-keyscan github.com >> /var/www/lifepath/.ssh/known_hosts'
sudo -u lifepath git clone git@github.com:icona-git/lifepath-calculator.git /var/www/lifepath/app
```

Then:
```bash
cd /var/www/lifepath/app
sudo -u lifepath python3 -m venv .venv
sudo -u lifepath .venv/bin/pip install --upgrade pip
sudo -u lifepath .venv/bin/pip install -r requirements.txt
sudo -u lifepath .venv/bin/python -c "import app; r = app.compute_results(app.SAMPLE_PROFILE); print('engine OK:', r['targets']['gross_mid'])"
```

## 3. Hardened systemd service

```bash
sudo tee /etc/systemd/system/lifepath.service > /dev/null <<'EOF'
[Unit]
Description=LifePath Calculator (Streamlit)
After=network.target

[Service]
User=lifepath
Group=lifepath
WorkingDirectory=/var/www/lifepath/app
Environment=HOME=/var/www/lifepath
ExecStart=/var/www/lifepath/app/.venv/bin/streamlit run app.py --server.address 127.0.0.1 --server.port 8501
Restart=always
RestartSec=3
UMask=0077

# Resource guard — shared box; Rae + ICONA production tenants live here
MemoryHigh=768M
MemoryMax=1G
CPUQuota=150%

# Sandbox
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ReadWritePaths=/var/www/lifepath
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now lifepath
systemctl status lifepath --no-pager
curl -s -o /dev/null -w "local app: HTTP %{http_code}\n" http://127.0.0.1:8501/
```

If the service fails to start, check `journalctl -u lifepath -n 30` — if a sandbox directive is the
cause, loosen ONLY the flagged one rather than removing the block.

## 4. Rate limit zone (own file, like rae-ratelimit.conf)

```bash
sudo tee /etc/nginx/conf.d/lifepath-ratelimit.conf > /dev/null <<'EOF'
limit_req_zone $binary_remote_addr zone=lifepath:10m rate=10r/s;
EOF
```

## 5. nginx vhost

```bash
sudo tee /etc/nginx/sites-available/life.appi.ca > /dev/null <<'EOF'
server {
    listen 80;
    listen [::]:80;
    server_name life.appi.ca;

    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;
    # active once certbot moves this block to 443; ignored on plain http
    add_header Strict-Transport-Security "max-age=31536000" always;

    location / {
        limit_req zone=lifepath burst=40 nodelay;
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # websockets — Streamlit is blank without these
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }
}
EOF
sudo ln -s /etc/nginx/sites-available/life.appi.ca /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

`nginx -t` passing = zero impact on the three live sites; reload is graceful.

## 6. TLS (certbot already installed + timer active)

```bash
sudo certbot --nginx -d life.appi.ca --redirect -m dave@icona.ca --agree-tos --no-eff-email
```

## 7. Verify — the new site AND the neighbours

```bash
curl -sI https://life.appi.ca/ | head -3
curl -s https://life.appi.ca/ | grep -o "<title>[^<]*</title>"
journalctl -u lifepath -n 10 --no-pager
# tenants unaffected:
for d in trade.lavellehealth.ca intake.lavellehealth.ca books.icona.ca; do
  echo -n "$d: "; curl -s -o /dev/null -w "%{http_code}\n" "https://$d/"
done
```

Browser check: https://life.appi.ca renders the welcome page (blank page = websocket headers not
applying) and one demo persona runs end to end.

## 8. Updates (each deploy)

```bash
cd /var/www/lifepath/app
sudo -u lifepath git rev-parse --short HEAD      # rollback point
sudo -u lifepath git pull
sudo -u lifepath .venv/bin/pip install -r requirements.txt --quiet
sudo systemctl restart lifepath
curl -s -o /dev/null -w "HTTP %{http_code}\n" https://life.appi.ca/
```

**Rollback:** `sudo -u lifepath git checkout <old-sha> && sudo systemctl restart lifepath`

## Optional extras

**Basic auth while iterating** (inside the `location /` block):
```bash
sudo apt install -y apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd-lifepath dave
#   auth_basic "LifePath preview";
#   auth_basic_user_file /etc/nginx/.htpasswd-lifepath;
```

**fail2ban jail for rate-limit abusers** (shared filter — bans IPs tripping ANY nginx limit_req
zone on the box, so keep maxretry generous):
```bash
sudo tee /etc/fail2ban/jail.d/nginx-limit-req.conf > /dev/null <<'EOF'
[nginx-limit-req]
enabled = true
filter = nginx-limit-req
logpath = /var/log/nginx/error.log
findtime = 600
maxretry = 60
bantime = 900
EOF
sudo systemctl reload fail2ban
```

**AWS Security Group:** ufw allows 22 from anywhere; if the SG does too, consider restricting 22
to known IPs there — or lean on the SSM agent (already running) and close 22 entirely.
