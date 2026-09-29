# Running Dispodex with Docker

One container runs the whole app: the web server and the background worker
(Square queue, nightly backup, daily checks). Everything that matters lives in
the `data` folder next to `docker-compose.yml`: the database, photos, backups
and logs. Back up that folder and you have backed up Dispodex.

## First-time setup on the server

Needs Docker with the Compose plugin (`docker compose version` should work).

```bash
git clone https://github.com/<your-account>/Dispodex.git
cd Dispodex
cp .env.example .env        # then edit .env: business name, sign-in, Square keys…
docker compose up -d --build
```

Open `http://<server address>:8765` from any computer or phone on the network.
If it doesn't load from another computer, open port 8765 in the server's
firewall (for example `sudo ufw allow 8765/tcp`).

To use another port, set `DISPODEX_PORT=8080` in `.env` (or in the shell) and
run `docker compose up -d` again.

## Moving the existing data in

Run only one Dispodex at a time: once the server copy is in use, stop the old
one for good, or the two will drift apart.

**Everything, photos included (recommended):**

1. On the old computer, close the Dispodex window so the database is closed cleanly.
2. Copy its whole `data` folder to the server, into the `Dispodex` folder, so you
   have `Dispodex/data/pinksheet.sqlite3`, `Dispodex/data/media/`, and so on.
3. On the server: `docker compose up -d` (or `docker compose restart` if it was already running).

The container gives the folder to its own user on the first start, and updates
the database automatically if it came from an older version (after backing it up).

**Only the database, from the browser:** open **System → Import database** and
choose a backup file from the old computer's `data/backups` folder. Photo files
aren't inside the database, so copy the old `data/media` folder to
`Dispodex/data/media` on the server as well.

## Everyday commands

Run these in the `Dispodex` folder on the server.

| To do this | Run |
|---|---|
| Start (and start again after a reboot) | `docker compose up -d` |
| Stop | `docker compose stop` |
| See what it's doing | `docker compose logs -f` |
| Update to the newest code | `git pull && docker compose up -d --build` |
| Back up now and check the backup | `docker compose exec dispodex python manage.py backup --verify` |
| Connect Square | `docker compose exec -it dispodex python manage.py configure_square`, then `docker compose restart` |
| Make a sign-in account | `docker compose exec -it dispodex python manage.py createsuperuser` |
| Put back the newest backup | `docker compose stop`, then `docker compose run --rm dispodex python manage.py restore_backup --latest`, then `docker compose up -d` |

`restart: unless-stopped` brings Dispodex back after a crash or a server
reboot. Docker checks `/api/health/` every 30 seconds; `docker compose ps`
shows it as `healthy`.

## Good to know

- **Backups** are written to `data/backups` every night at `BACKUP_HOUR` (2 AM unless changed in `.env`).
  They are on the same disk as the database, so also copy the `data` folder
  somewhere else regularly, or set `BACKUP_MIRROR_DIR` to a folder on another
  disk that is mounted into the container.
- **Settings**: `docker-compose.yml` always sets the data folder, host and port
  inside the container, so paths in `.env` from a Windows install are ignored.
  After changing `.env`, run `docker compose up -d` to apply it.
- **Shop-only actions** (backups, import, full Square sync) are refused from
  addresses outside the local network or Tailscale. Docker on Linux keeps each
  visitor's real address, so this works as it does without Docker.
- **Time zone**: set `PINKSHEET_TIME_ZONE` in `.env` (and `TZ` for log times; both
  default to America/Chicago).
