# Running Dispodex

## The one folder that matters: `data/`

| Path | What |
|---|---|
| `data/pinksheet.sqlite3` | The database (never copy it while running; use a backup instead) |
| `data/media/sku_photos/<SKU>/` | Photos (same layout as the old app) |
| `data/media/ebay_images/` | Listing-image composer uploads |
| `data/media/doc_photos/<post id>/` | Photos on Team docs posts |
| `data/backups/` | Nightly backups, each with a `.sha256` checksum |
| `data/logs/` | `app.log`, `square_sync.log`, `lookup.log` (rotated automatically) |
| `data/secret_key.txt` | Created on first run; keeps sign-ins valid across restarts |
| `data/qz-signing/` | Optional QZ Tray certificate and private key for silent label printing |

## Backups

- **Automatic:** every night at `BACKUP_HOUR` (2 AM) while Dispodex is running. If the computer
  was off, the backup runs as soon as it starts again.
- **By hand:** *Back up now* on the dashboard or System page, or `manage.py backup --verify`.
- **Off-site copy:** set `BACKUP_MIRROR_DIR` (for example a OneDrive folder) and, for photos,
  `BACKUP_PHOTOS_MIRROR` in `.env`.
- **Keeping only recent backups:** `BACKUP_KEEP=30` keeps the newest 30. The default `0` keeps all of them.

Backups use SQLite's `VACUUM INTO`, which makes a consistent copy even while people are saving.

**After an update:** if a new version changes the database, `start.bat` backs it up first
("Database update found: backed up first" in the window), then applies the change. If that
backup fails, Dispodex stops without changing anything.

### Restoring

1. Close the Dispodex window (stop the server).
2. `.venv\Scripts\python.exe manage.py restore_backup --latest`
   (or give a file name from `data/backups`).
3. Type `RESTORE`. The current database is saved next to it as `pinksheet-before-restore-<time>.sqlite3` first.
4. Start Dispodex again.

## Health checks

- The **System** page shows the worker, database integrity, photo pipeline, disk space,
  Square and the queue, and backups.
- `GET /api/health/` returns JSON, which is useful for a monitoring tool.
- The dashboard warns when the newest backup is more than 36 hours old or the disk is under 10% free.

## Settings you might change (`.env`)

| Setting | Default | Notes |
|---|---|---|
| `PINKSHEET_PORT` | 8765 | Same port as the old app |
| `PINKSHEET_REQUIRE_LOGIN` | 0 | 1 = everyone signs in |
| `PINKSHEET_MAINTENANCE_MODE` | 0 | 1 = show a "down for maintenance" page |
| `PINKSHEET_PHOTO_CONVERT_TO_PNG` | 1 | Old app stored everything as PNG |
| `PINKSHEET_PHOTO_MAX_DIMENSION` | 1200 | Longest side in pixels |
| `BACKUP_HOUR` / `RECONCILE_HOUR` | 2 / 3 | Local time |

Restart Dispodex after editing `.env`.

## Label printing (Zebra + QZ Tray)

1. Install [QZ Tray](https://qz.io/download/) on the computer with the Zebra printer.
2. Printing works right away, but QZ Tray asks "Allow?" each time. To make it silent, generate a
   certificate in QZ Tray (*Advanced → Site Manager*) and save the two files as
   `data/qz-signing/digital-certificate.txt` and `data/qz-signing/private-key.pem`.
3. The printer whose name contains "Zebra" (or "ZPL") is picked automatically.

## Troubleshooting

| Symptom | Check |
|---|---|
| Other computers can't connect | Windows Firewall: allow Python on private networks, port 8765 |
| "Background worker: Not running" | Dispodex was started with `runserver` instead of `start.bat` |
| Square jobs pile up | System page: read the last error. Usually a wrong token or location |
| Photo upload fails | The file must be a real JPG, PNG, WebP or GIF under 32 MB. HEIC photos must be converted by the phone (iPhone: Settings → Camera → Formats → Most Compatible) |
| Something else | `data/logs/app.log` has the details |
