# Imaging reports

An imaging app (for example a PXE deployment script) can send Dispodex what it
finds about each computer while it images it. Reports show on the **Imaging**
page with the imaging progress. Once a computer is linked to an item, its specs
fill in that item's blank fields; what staff typed is never overwritten, and
the change shows in the item's History as "Imaging".

## Turn it on

1. Make a secret key:

   ```
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. Put it in `.env` and restart Dispodex:

   ```
   PINKSHEET_IMAGING_API_KEY=the-key-you-made
   ```

3. Give the same key to the imaging app. Reports are only accepted from the
   local network (or the shop VPN), and only with the key, even when sign-in is
   required for people.

## Sending a report

`POST http://<dispodex-server>:8765/api/imaging/report/` with a JSON body and
the key in an `Authorization: Bearer <key>` header (or `X-Api-Key: <key>`).
Send one object, or a list of up to 100.

```json
{
  "serial": "TEST5500A",
  "manufacturer": "Dell Inc.",
  "model": "Latitude 5500",
  "cpu": "Intel Core i5-8365U",
  "ram_gb": 8,
  "disk_model": "SAMSUNG MZVLB256HBHQ",
  "disk_gb": 256,
  "disk_bus": "NVMe",
  "windows_edition": "Windows 11 Pro",
  "battery_present": true,
  "battery_health_pct": 91,
  "status": "Imaging",
  "stage": "Hardware detected",
  "progress": 5,
  "message": "PXE deployment started"
}
```

Only `serial` is required. Send the same serial again as imaging moves along;
an update can carry just the serial and the progress fields, and the hardware
details from earlier reports are kept. Any other fields (MAC address, TPM,
disk serial…) are stored and shown under **All details** on the Imaging page.

| JSON field | Item field | Example of what's saved |
|---|---|---|
| `serial` | Serial number | `TEST5500A` |
| `manufacturer` + `model` | Brand & model | `Dell Latitude 5500` |
| `cpu` | CPU | `i5-8365U` |
| `ram_gb` | RAM | `8GB` |
| `disk_gb` | SSD GB | `256GB` |
| `windows_edition` | OS | `Windows 11 Pro` |
| `battery_health_pct` | Battery health | `91%` |

The answer is JSON: `{"ok": true, "received": 1, "computers": [{"serial": "TEST5500A", "item": "LAP-1"}]}`
(`item` is `null` until the computer is linked). Errors come back as
`{"ok": false, "error": "..."}` with status 400 (bad JSON or no serial),
401 (wrong key), 403 (not from the local network) or 503 (no key set yet).

### PowerShell (WinPE or Windows)

```powershell
$report = @{
  serial = (Get-CimInstance Win32_BIOS).SerialNumber
  manufacturer = (Get-CimInstance Win32_ComputerSystem).Manufacturer
  model = (Get-CimInstance Win32_ComputerSystem).Model
  status = "Imaging"; stage = "Hardware detected"; progress = 5
}
Invoke-RestMethod -Method Post -Uri "http://dispodex:8765/api/imaging/report/" `
  -Headers @{ Authorization = "Bearer $env:DISPODEX_KEY" } `
  -ContentType "application/json" -Body ($report | ConvertTo-Json)
```

### curl

```
curl -X POST http://dispodex:8765/api/imaging/report/ \
  -H "Authorization: Bearer $DISPODEX_KEY" -H "Content-Type: application/json" \
  -d @report.json
```

## Without the network

On the Imaging page, **Upload JSON** takes one or more `.json` files in the same
format. Uploaded reports are marked with the name of the person who uploaded them.

## Linking computers to items

- **Link**: type the item's SKU on the computer's card. Its serial number and
  specs are copied into the item's blank fields.
- **Start intake**: opens a new intake sheet with the specs filled in. Saving
  it links the computer to the new item.
- A computer whose serial number is already on an item links by itself.

After linking, later reports for that serial keep the item up to date (blank
fields only). **Unlink** and **Remove from Imaging** never change the item.
