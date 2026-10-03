# Developer guide

A standard Django project. If you know Django, you already know where everything is.

## Getting started

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 -Dev     # installs test tools too
.venv\Scripts\python.exe -m pytest                           # full test suite, about a minute
.venv\Scripts\python.exe manage.py runserver 8766            # dev server with auto-reload
.venv\Scripts\python.exe manage.py run_worker                # (optional) background jobs in a 2nd window
```

For development, put `DJANGO_DEBUG=1` in `.env` to get detailed error pages and uncached static files.

## Layout: one job per module

| App | Responsibility | Start reading at |
|---|---|---|
| `core` | SKU rules, JSON response shape, security headers, local-network check | `core/skus.py`, `core/http.py` |
| `inventory` | Items, photos, intake, lookup, board, exports, labels, script builder | `inventory/models.py`, `inventory/services/` |
| `archive` | Read-only legacy records and CSV import | `archive/views.py`, `archive/importer.py` |
| `squaresync` | Square API client, catalog sync, queue, webhooks, reconciliation | `squaresync/sync.py`, `squaresync/queue.py` |
| `operations` | Backups, health, the worker, `serve`, legacy import, System page, QZ signing | `operations/worker.py`, `operations/backups.py` |

**Views stay thin and business rules live in `services/`.** For example, saving an intake sheet goes
`views/intake.py` → `IntakeForm` (validation) → `services/items.save_intake()` (the rules) →
`squaresync.queue.enqueue()` (after commit). Change a rule in one place and every page gets it.

Front-end: plain JavaScript with no build step. Each page has one file in `static/js/`. Shared
helpers (`Dispodex.api`, `toast`, `confirm`, `lightbox`) are in `static/js/app.js`. All styling
is in `static/css/app.css`, driven by the colour tokens at the top. Dark mode only swaps the tokens.
Pages never use inline `<script>`; data is passed with `{{ value|json_script:"id" }}`.
This keeps the Content-Security-Policy strict.

## Rules worth knowing

- **SKUs** are always matched by `sku_normalized` (upper-case, trimmed). There is one live item per SKU,
  enforced by a database constraint. Deleted items are soft-deleted (`deleted_at`) so *Undo* works.
- **Photos belong to a SKU, not an item**, so they can be added before the item is saved.
  Files live in `data/media/sku_photos/<SKU>/`. Every upload is decoded by Pillow (non-images
  are rejected), rotated upright, shrunk to 3000 px on the longest side (sharp for eBay zoom) and saved as PNG, stepped down if over eBay's 12 MB limit.
- **Status values** are the six lanes in `inventory.models.Status`. `coerce_status()` maps
  old spellings ("SOLD", "Tested", "eBay Listed"). Moving to SOLD sets the SOLD badge.
- **Price** is a single field. The old app kept two identical columns.
- **Square never blocks a request.** Use `squaresync.queue.enqueue(sku)`. The worker does the rest.
- **ZPL labels** must stay byte-identical to the old app. `tests/fixtures/zpl_cases.json` was
  generated from the PHP code, and a test compares against it.
- **The eBay script builder text** in `inventory/services/scripts.py` was copied word for word.
- **Item history:** any code that changes an item must record it. Take
  `before = history.snapshot(item)` before changing it, then call
  `history.record(item, ItemEvent.Action.EDITED, before=before)` after saving. The service
  functions in `inventory/services/items.py` already do this. Background code passes
  `actor="Square"` (or similar). In a request, `core.actor.ActorMiddleware` works out who is
  acting: the signed-in user, else the name saved on that device (`ps_name` cookie), else the
  device's address.
- **Layout works at every width.** Use the shared layout classes (`.grid-3`, `.stats`,
  `.two-col`, `.head-actions`, `.toolbar`), which already wrap. Don't set fixed column widths
  in inline styles. Hover-only buttons must also show under `@media (hover: none)`. After UI
  changes, check the page at desktop, tablet (820 px) and phone (375 px) widths.

## Adding a field to items

1. Add it to `Item` in `inventory/models.py`.
2. `manage.py makemigrations inventory` and then `migrate`.
3. Add it to `IntakeForm.Meta.fields` and draw it in `templates/inventory/intake.html`.
   Add it to `TRACKED_FIELDS` in `inventory/services/history.py` so changes show in History.
4. If it should appear elsewhere, add it to `exports.EXPORT_COLUMNS`, `squaresync/sync.py`
   (`HASH_FIELDS` and `DESCRIPTION_LABELS`), `drafts.ALLOWED_FIELDS`, and the legacy importer.
5. Add a test.

## Tests

`tests/` covers intake saving, drafts and conflicts, photos, search, exports, the archive import,
labels (against the PHP output), every Square path (with mocked HTTP), webhooks, reconciliation,
backups, QZ signing, and the legacy import. Run them before every change you ship.

## What changed from the PHP app (on purpose)

- One Python process (`serve`) runs the website and the background worker, so there are no
  scheduled-task scripts to keep in sync.
- Square pushes always go through the queue. Re-saving a SKU now really re-queues it (the PHP
  queue silently ignored repeats), and jobs wait instead of failing while Square isn't configured.
- Autosave drafts detect when two people edit the same SKU (the PHP docs promised this, but it
  was never built).
- The script builder saves to its own table (PHP overwrote intake drafts with script text).
- Renaming a SKU moves its photos with it.
- A Square refund moves the item back to Intake with a note, so it shows on the board.
- Inventory webhooks only react to `IN_STOCK` counts, so waste or sold counts can't mark items SOLD by mistake.
- Optional sign-in (`PINKSHEET_REQUIRE_LOGIN=1`) and a Django admin at `/admin/`.
- **New:** each item has a History panel (on the sheet and the phone card) showing every change,
  who made it and when, including sales and refunds coming from Square.
- **New:** Lookup can select many items and move them to a stage or mark them ready together.
- **New:** board cards have a *Move* button (a lane picker), so cards can be moved on phones and
  tablets, where drag-and-drop doesn't work.
- **New:** every screen adapts from wide monitors down to phones. On touch screens, buttons are
  bigger and the hover-only buttons are always shown.
- **New:** starting Dispodex after an update that changes the database takes a backup first.
- **Renamed to Dispodex** on screen (sidebar, tab titles, printouts, home-screen icon, Square
  descriptions). Internal names stay `pinksheet` on purpose: the code package, `PINKSHEET_*`
  settings, the database file `data/pinksheet.sqlite3`, backup file names and `window.Pinksheet`
  in JavaScript. Renaming those would break existing installs, backups and `.env` files.
- **Condition scale** is Scrap, Poor, Fair, Good, Great, Excellent (was Good, Great, Excellent,
  Unicorn). Old "Unicorn" items become Excellent, both in the database update and in the PHP import.
- **The "Is it a Square item?" / "Do we care about this item?" boxes were removed** from the sheet,
  exports and Square descriptions. The columns stay in the database so older answers aren't lost
  (visible in `/admin/`). They never controlled whether an item syncs to Square.
- **Status board:** SKUs, names and brand/model are never cut off (they wrap). Card actions (Move,
  QR, print, open, delete) live in each card's ⋯ menu, pinned beside the SKU. Lanes can be folded
  to a slim strip and there is a Detailed / Compact view; both are remembered per device in
  `localStorage` (`dispodex:board-collapsed`, `dispodex:board-view`). Lane headers show the total
  value (price × quantity) of the cards showing.
- **Lookup search:** the main box searches `SEARCH_FIELDS` in `inventory/services/search.py`, and
  every word typed must match somewhere. "More filters" (`TEXT_FILTERS` plus condition,
  functional, ready and date received) are part of `ItemFilters`, so exports and shared links
  honour them too. To make a new field searchable, add it to those lists.
- The logo lives in `static/img/logo.svg` (favicon.svg is a copy). The PNG home-screen icons in
  `static/img/` were rendered from it; re-render them if the logo changes.
- Not carried over: the unused `devices` / `diagnostic_sessions` tables (they were empty), and the
  Google Sheets sync script.
