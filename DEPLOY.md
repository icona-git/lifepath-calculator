# Deploying to life.appi.ca (Ubuntu + nginx + systemd)

Target: 99.79.133.144 (EC2 ca-central-1), Ubuntu, nginx 1.24, DNS `life.appi.ca` → A record live.
The app runs as a systemd service bound to 127.0.0.1:8501; nginx terminates TLS and proxies
with websocket pass-through (required — without the Upgrade headers Streamlit loads a blank shell).

## 0. Repo access (run LOCALLY, pick one)

**A — make the repo public (simplest; it's a public prototype, no secrets in the repo):**
```bash
gh repo edit icona-git/lifepath-calculator --visibility public --accept-visibility-change-consequences
```

**B — keep it private, use a read-only deploy key** (generate on the server in step 2, then locally):
```bash
gh repo deploy-key add /path/to/copied/id_ed25519.pub --repo icona-git/lifepath-calculator --title "life.appi.ca"
```

## 1. System prep (on the server)

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip git nginx certbot python3-certbot-nginx
sudo useradd --system --create-home --home-dir /opt/lifepath --shell /usr/sbin/nologin lifepath
```

## 2. Clone

**A — public repo:**
```bash
sudo -u lifepath git clone https://github.com/icona-git/lifepath-calculator.git /opt/lifepath/app
```

**B — private repo via deploy key:**
```bash
sudo -u lifepath mkdir -p /opt/lifepath/.ssh && sudo -u lifepath chmod 700 /opt/lifepath/.ssh
sudo -u lifepath ssh-keygen -t ed25519 -f /opt/lifepath/.ssh/id_ed25519 -N "" -C "lifepath-deploy@life.appi.ca"
sudo cat /opt/lifepath/.ssh/id_ed25519.pub   # ← add this as a read-only deploy key (step 0B)
sudo -u lifepath bash -c 'ssh-keyscan github.com >> /opt/lifepath/.ssh/known_hosts'
sudo -u lifepath git clone git@github.com:icona-git/lifepath-calculator.git /opt/lifepath/app
```

## 3. Python env + engine self-test

```bash
cd /opt/lifepath/app
sudo -u lifepath python3 -m venv .venv
sudo -u lifepath .venv/bin/pip install --upgrade pip
sudo -u lifepath .venv/bin/pip install -r requirements.txt
sudo -u lifepath .venv/bin/python -c "import app; r = app.compute_results(app.SAMPLE_PROFILE); print('engine OK, gross mid:', r['targets']['gross_mid'])"
```

## 4. systemd service

```bash
sudo tee /etc/systemd/system/lifepath.service > /dev/null <<'EOF'
[Unit]
Description=LifePath Calculator (Streamlit)
After=network.target

[Service]
User=lifepath
WorkingDirectory=/opt/lifepath/app
ExecStart=/opt/lifepath/app/.venv/bin/streamlit run app.py --server.address 127.0.0.1 --server.port 8501
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now lifepath
systemctl status lifepath --no-pager
curl -s -o /dev/null -w "local app: HTTP %{http_code}\n" http://127.0.0.1:8501/
```

## 5. nginx site (HTTP first)

```bash
sudo tee /etc/nginx/sites-available/life.appi.ca > /dev/null <<'EOF'
server {
    listen 80;
    server_name life.appi.ca;

    location / {
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
curl -sI http://life.appi.ca/ | head -3
```

`nginx -t` passing means zero impact on whatever else this box serves — the new server block
only answers for `life.appi.ca`.

## 6. TLS (DNS already resolves, so HTTP-01 will pass; auto-renews)

```bash
sudo certbot --nginx -d life.appi.ca --redirect -m dave@icona.ca --agree-tos --no-eff-email
curl -sI https://life.appi.ca/ | head -3
```

## 7. Verify what's actually served (always)

```bash
systemctl status lifepath --no-pager
journalctl -u lifepath -n 20 --no-pager
curl -s https://life.appi.ca/ | grep -o "<title>[^<]*</title>"
```

Then in a real browser: https://life.appi.ca should show the welcome page (a blank page = websocket
headers missing), and a demo persona should run end to end.

## 8. Updating after a push (each deploy)

```bash
cd /opt/lifepath/app
sudo -u lifepath git rev-parse --short HEAD      # note as rollback point
sudo -u lifepath git pull
sudo -u lifepath .venv/bin/pip install -r requirements.txt --quiet
sudo systemctl restart lifepath
curl -s -o /dev/null -w "HTTP %{http_code}\n" https://life.appi.ca/
```

**Rollback:** `sudo -u lifepath git checkout <old-sha> && sudo systemctl restart lifepath`

## Optional: gate it while iterating (basic auth at the proxy)

```bash
sudo apt install -y apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd-lifepath dave
# then inside the location / block add:
#   auth_basic "LifePath preview";
#   auth_basic_user_file /etc/nginx/.htpasswd-lifepath;
sudo nginx -t && sudo systemctl reload nginx
```

Notes: the app stores nothing server-side (session-only, no DB); headings/mono fonts load from
Google Fonts in the visitor's browser; the prototype intentionally has no auth of its own.
