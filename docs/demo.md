# Public demo

A demo copy of Dispodex that anyone can click around in: made-up items, reset every night.
**Run it on its own machine or container with its own folder, never on the shop server.**

With `PINKSHEET_DEMO_MODE=1`:

- `manage.py seed_demo` wipes everything and fills it with 48 made-up items across all six
  stages, with simple drawn product pictures and history. It refuses to run without demo mode.
- A banner on every page says it is a demo that resets nightly.
- Photo and listing-image uploads are turned off, so strangers can't post pictures for others to see.
- Operator actions (backups, Square settings, reconciliation) are turned off, even from localhost.
- `serve` may be reached over HTTPS without sign-in, because the data is fake.

## Set up (Debian/Ubuntu VM or LXC on Proxmox)

```bash
sudo apt install -y python3 python3-venv git
sudo git clone https://github.com/g39832/Dispodex.git /opt/dispodex-demo
sudo chown -R $USER /opt/dispodex-demo && cd /opt/dispodex-demo
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
```

In `.env`:

```
PINKSHEET_BUSINESS_NAME=Demo Refurb Co.
PINKSHEET_DEMO_MODE=1
SQUARE_SYNC_ENABLED=0
PINKSHEET_HOST=127.0.0.1
PINKSHEET_PORT=8765
# When it's online behind the tunnel below:
PINKSHEET_BEHIND_HTTPS_PROXY=1
DJANGO_ALLOWED_HOSTS=demo.graysoncodes.org
DJANGO_CSRF_TRUSTED_ORIGINS=https://demo.graysoncodes.org
```

Then fill it and start it:

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo
.venv/bin/python manage.py serve
```

## Keep it running and reset it nightly

`/etc/systemd/system/dispodex-demo.service`:

```ini
[Unit]
Description=Dispodex public demo
After=network-online.target

[Service]
WorkingDirectory=/opt/dispodex-demo
ExecStart=/opt/dispodex-demo/.venv/bin/python manage.py serve
Restart=always
User=YOUR_USER

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now dispodex-demo
# reset at 3 AM every night
( crontab -l 2>/dev/null; echo "0 3 * * * cd /opt/dispodex-demo && .venv/bin/python manage.py seed_demo" ) | crontab -
```

## Put it online

The simplest way is a Cloudflare Tunnel (no ports opened on your router): in the Cloudflare
dashboard go to **Zero Trust → Networks → Tunnels**, create a tunnel, install the connector it
shows on the demo machine, and add a public hostname `demo.graysoncodes.org` pointing to
`http://127.0.0.1:8765`.
