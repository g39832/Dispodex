# Square

Square is optional. Without it, everything else works and Square jobs simply wait in the queue.

## What syncs

**Dispodex → Square** (every save, status change, price change or photo change):

- One Square item per SKU, named "Brand & model - What is it". The variation SKU is the Dispodex SKU.
- The price (or variable pricing if no price is set), a full description, and the thumbnail photo.
- The stock count is the quantity, or **0 once the item is SOLD**.

The web page never waits for Square. Saves add a job to a queue, and the background worker
(started by `start.bat`) sends it within a second or two. Failures retry with growing delays
(30 s, 1 min, 2 min … up to 8 h). After 10 tries a job is marked "gave up" and shown on the
**System** page with a *Retry failed jobs* button.

**Square → Dispodex** (needs webhooks, below):

- A completed sale records the sale and marks the item SOLD.
- A completed refund moves the item back to **Intake** and adds the note
  "Returned via Square refund — needs inspection".
- Stock changes made in Square are applied if they are newer than the last Dispodex change.

A daily **reconciliation** (3 AM by default, or *Run check now* on the dashboard) looks for
items that were never synced, sync errors that keep coming back, sales not marked SOLD, missing
photos in Square, and Square items with no Dispodex record. It fixes the safe ones and lists the rest.

## Setting it up

1. In the [Square Developer Dashboard](https://developer.squareup.com/apps), open your app →
   **Credentials**. Copy the **access token** (use Sandbox first to test).
2. Find the **location ID** under **Locations**.
3. Run `.venv\Scripts\python.exe manage.py configure_square`, or edit `.env`:

   ```
   SQUARE_ENVIRONMENT=sandbox        # production for the real store
   SQUARE_ACCESS_TOKEN=EAAA...
   SQUARE_LOCATION_ID=L...
   ```

4. Restart Dispodex, then open **System → Test connection**.
5. To send everything that already exists, use **System → Queue a full sync**.

## Webhooks (sales coming back)

Square can only call Dispodex over **public HTTPS**. The usual way to get that on a shop
computer is a Cloudflare Tunnel (or ngrok) pointing at `http://localhost:8765`.

1. Expose **only** the path `/webhooks/square/` through the tunnel if you can.
2. In Square → **Webhooks**, add a subscription for
   `https://YOUR-PUBLIC-ADDRESS/webhooks/square/` with these events:
   `payment.updated`, `refund.updated`, `order.updated`, `inventory.count.updated`, `catalog.version.updated`.
3. Put the subscription's signature key and the exact URL in `.env`:

   ```
   SQUARE_WEBHOOK_SIGNATURE_KEY=...
   SQUARE_WEBHOOK_NOTIFICATION_URL=https://YOUR-PUBLIC-ADDRESS/webhooks/square/
   PINKSHEET_BEHIND_HTTPS_PROXY=1
   DJANGO_CSRF_TRUSTED_ORIGINS=https://YOUR-PUBLIC-ADDRESS
   ```

4. Restart and click **Send test event** in Square. `data/logs/square_sync.log` records it.

Every webhook is checked against its signature. Events older than 3 days are rejected, and
duplicates are ignored. If the old PHP subscription URL (`/webhooks/square.php`) is still
registered, it keeps working, but the notification URL in `.env` must match it exactly.

> Exposing the whole app to the internet? Turn on `PINKSHEET_REQUIRE_LOGIN=1` and create
> accounts with `manage.py createsuperuser`. The old app had no login at all.

## Logs

- `data/logs/square_sync.log`: one line per Square call and sync (JSON)
- **System** page: the waiting and failed jobs, recent activity, and reconciliation runs
- `/admin/`: every sync record, job, sale and webhook event
