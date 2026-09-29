# Dispodex (Python / Django)

An inventory app for a computer refurbishing shop: intake sheets, photos, status board, lookup,
archive, eBay script builder, Zebra labels, backups and optional two-way
Square sync. It is a Python rewrite of an older PHP app, which it leaves untouched.

## About this project

Dispodex replaced a PHP + SQLite tool used daily by a small team on a local network. Highlights:

- **Intake sheet** with autosaved drafts, conflict detection when two people edit the same SKU,
  drag-and-drop photo uploads (resized server-side with Pillow) and a phone-friendly QR card.
- **Status board** (kanban) with drag-and-drop, folding lanes, compact view and per-lane totals.
- **Lookup** searching every field with multi-word queries, 17 field filters, bulk changes and
  exports to CSV, ZIP and Excel with embedded photos.
- **Item history**: every change is recorded with who made it, old and new values, merged when
  one person makes quick successive edits.
- **Square integration** through a retrying job queue, signed webhooks and a nightly
  reconciliation that repairs safe mismatches.
- **Operations**: nightly verified SQLite backups, automatic backup before database updates,
  health checks, a background worker and a data importer that verifies every record and photo.
- **Responsive UI** tested from 320 px phones to wide monitors, light and dark themes.

**Tech:** Python 3.11, Django 5.2, SQLite (WAL), Waitress, WhiteNoise, Pillow, openpyxl,
vanilla JavaScript and CSS, pytest (190+ tests).

## Everyday use

| To do this | Do this |
|---|---|
| Start Dispodex | Double-click **`start.bat`** and keep the window open |
| Open it on this computer | http://localhost:8765 |
| Open it on another computer or phone | The "On the shop network" address printed in the start window |
| Stop it | Close the start window (or press Ctrl+C in it) |

**Tip for each computer or phone:** click **Set your name** at the bottom of the sidebar (or on
a phone card) once. Your changes then show with your name in each item's **History**.

Printed QR codes and old bookmarks from the PHP app (`card.php`, `intake.php`, `photo.php` …) still work: they redirect to the new pages.

## First-time setup (once per computer)

1. Install **Python 3.10 or newer** from python.org (tick "Add python.exe to PATH").
2. Right-click **`setup.ps1`** → *Run with PowerShell*. It installs everything, creates `.env`,
   builds the database, and offers to copy all data from the old PHP Pinksheet.
3. Double-click **`start.bat`**.

Optional: to start Dispodex automatically with Windows, run
`scripts\install_startup_task.ps1` once in PowerShell.

## Running on a server with Docker

```bash
cp .env.example .env
docker compose up -d --build
```

Then open `http://<server address>:8765`. The database, photos and backups live in `./data`.

## Connecting Square (whenever you're ready)

The app works fully without Square. Changes made before Square is connected are queued and sent
when it is. To connect it:

```powershell
.venv\Scripts\python.exe manage.py configure_square
```

Answer the questions (access token, location ID, sandbox or production), restart Dispodex, then
go to **System → Test connection**. You can also paste the values straight into `.env`.
See [docs/square.md](docs/square.md) for webhooks (sales flowing back from the Square POS).

## Where things live

```
dispodex/
├── start.bat / setup.ps1     start & install
├── .env                      your settings and keys (never share or commit this)
├── data/                     database, photos, backups, logs  ← back this folder up
├── pinksheet/                Django settings and URL list
├── core/                     shared helpers (SKU rules, security headers, JSON replies)
├── inventory/                items, photos, intake, lookup, board, exports, labels, scripts
├── archive/                  legacy history + CSV import
├── squaresync/               Square client, sync queue, webhooks, reconciliation
├── operations/               backups, health checks, background worker, legacy import, `serve`
├── templates/ static/        pages, CSS (one design system in static/css/app.css), JavaScript
├── tests/                    automated tests (run: .venv\Scripts\python.exe -m pytest)
└── docs/                     guides for operators and developers
```

## Useful commands

All commands are run from this folder as `.venv\Scripts\python.exe manage.py <command>`:

| Command | What it does |
|---|---|
| `serve` | Run the app plus the background worker (what `start.bat` does) |
| `backup --verify` | Back up the database now and check it |
| `restore_backup --latest` | Put back the newest backup (stop Dispodex first) |
| `import_legacy "C:\path\to\old\pinksheet"` | Copy data from the old PHP app (`--replace` to redo) |
| `import_archive_csv file.csv --source "Old Server"` | Add old records to the Archive |
| `configure_square` | Enter Square keys |
| `sync_square --all` | Push every item to Square right now |
| `reconcile_square` | Compare Dispodex with Square and fix what is safe |
| `refresh_ebay_categories` | Download eBay's current category list |
| `createsuperuser` | Make a login for `/admin/` (or for sign-in, if you turn it on) |
| `seed_demo` | Demo copies only: replace everything with made-up items (see docs/demo.md) |

More detail: [docs/operations.md](docs/operations.md) (backups, restore, troubleshooting) and
[docs/development.md](docs/development.md) (how the code is organised, how to change it safely).
To run a public demo copy with made-up items that resets nightly, see [docs/demo.md](docs/demo.md).

<!-- Test push after recreating the repo (2026-09-26) -->
