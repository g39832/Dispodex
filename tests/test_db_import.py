"""The System page's "Import database" button."""
import shutil
import sqlite3
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from inventory.models import Item
from operations import backups, db_import
from operations.models import SystemState
from tests.test_operations import build_legacy_app

# The import writes the live database file through SQLite, so it can't run inside a test transaction.
pytestmark = pytest.mark.django_db(transaction=True)


def _upload(client, path, name="pinksheet.sqlite3"):
    file = SimpleUploadedFile(name, path.read_bytes(), content_type="application/octet-stream")
    return client.post(reverse("api_import_database"), {"file": file}, HTTP_COOKIE="ps_name=Sam")


def test_import_dispodex_database_replaces_the_data(client):
    Item.objects.create(sku="keep-1", what_is_it="Laptop")
    snapshot = backups.run_backup().path
    Item.objects.all().delete()
    Item.objects.create(sku="new-2", what_is_it="Monitor")

    response = _upload(client, snapshot)

    assert response.status_code == 200, response.content
    data = response.json()
    assert data["kind"] == "dispodex" and data["items"] == 1
    assert list(Item.objects.values_list("sku_normalized", flat=True)) == ["KEEP-1"]
    assert "Sam" in SystemState.get(db_import.LAST_IMPORT_KEY)
    # The data from before the import was backed up and still holds the replaced item.
    con = sqlite3.connect(backups.backup_dir() / data["backup"])
    assert con.execute("SELECT sku_normalized FROM inventory_item").fetchall() == [("NEW-2",)]
    con.close()


def test_import_old_pinksheet_database(client, tmp_path):
    build_legacy_app(tmp_path)
    Item.objects.create(sku="old-9")

    response = _upload(client, tmp_path / "data" / "intake.sqlite", name="intake.sqlite")

    assert response.status_code == 200, response.content
    assert response.json()["kind"] == "legacy"
    assert set(Item.objects.values_list("sku_normalized", flat=True)) == {"D-1", "X-2"}


def test_import_refuses_files_that_are_not_databases(client, tmp_path):
    Item.objects.create(sku="stay-1")
    junk = tmp_path / "notes.sqlite3"
    junk.write_text("hello")
    other = tmp_path / "other.sqlite3"
    sqlite3.connect(other).execute("CREATE TABLE t (x)").connection.close()

    for path in (junk, other):
        response = _upload(client, path)
        assert response.status_code == 400
    assert Item.objects.filter(sku_normalized="STAY-1").exists()
    assert backups.list_backups() == []  # refused before anything happened


def test_import_refuses_a_database_from_a_newer_version(client, tmp_path):
    Item.objects.create(sku="stay-1")
    newer = tmp_path / "newer.sqlite3"
    shutil.copy2(backups.run_backup().path, newer)
    con = sqlite3.connect(newer)
    con.execute("INSERT INTO django_migrations (app, name, applied) VALUES ('inventory', '9999_future', '2030-01-01')")
    con.commit()
    con.close()

    response = _upload(client, newer)

    assert response.status_code == 400 and "newer version" in response.json()["error"]
    assert Item.objects.filter(sku_normalized="STAY-1").exists()


def test_failed_import_puts_the_data_back(client, monkeypatch):
    Item.objects.create(sku="keep-1")
    snapshot = backups.run_backup().path
    Item.objects.create(sku="also-2")
    monkeypatch.setattr(db_import, "_item_count", lambda path: 999)  # force the check after the swap to fail

    response = _upload(client, snapshot)

    assert response.status_code == 400 and "put back" in response.json()["error"]
    assert set(Item.objects.values_list("sku_normalized", flat=True)) == {"KEEP-1", "ALSO-2"}


def test_import_blocked_from_outside_network(tmp_path):
    path = tmp_path / "x.sqlite3"
    path.write_bytes(b"")
    assert _upload(Client(REMOTE_ADDR="8.8.4.4"), path).status_code == 403


def test_system_page_has_the_import_button(client):
    assert b'id="import-database"' in client.get(reverse("system")).content


# ── spreadsheets and other table files ───────────────────────────────────────
def _post(client, name, content: bytes):
    file = SimpleUploadedFile(name, content, content_type="application/octet-stream")
    return client.post(reverse("api_import_database"), {"file": file}, HTTP_COOKIE="ps_name=Sam")


def test_csv_adds_new_skus_and_updates_known_ones(client):
    Item.objects.create(sku="old-1", what_is_it="Laptop", brand_model="Dell", price="50.00")
    Item.objects.create(sku="untouched-9", what_is_it="Mouse")
    csv_text = (
        "Item SKU,Name,Brand/Model,Price,Qty,Condition,Status,Power On,Warehouse Row\r\n"
        "old-1,,,$75.50,,great,Listed,y,\r\n"
        "new-2,Monitor,LG 24,120,3,Unicorn,eBay Draft,No,B7\r\n"
        ",Nothing,,,,,,,\r\n"
    )

    response = _post(client, "stock.csv", csv_text.encode("utf-8-sig"))

    assert response.status_code == 200, response.content
    assert response.json()["kind"] == "sheet"
    old = Item.objects.get(sku_normalized="OLD-1")
    assert old.what_is_it == "Laptop" and old.brand_model == "Dell"  # blank cells never erase
    assert str(old.price) == "75.50" and old.condition == "Great" and old.status == "ebay listed" and old.power_on == "Yes"
    new = Item.objects.get(sku_normalized="NEW-2")
    assert (new.what_is_it, new.quantity, new.condition, new.status) == ("Monitor", 3, "Excellent", "ebay draft")
    assert "Warehouse Row: B7" in new.notes  # unknown columns are kept, not lost
    assert Item.objects.filter(sku_normalized="UNTOUCHED-9").exists()
    report = " ".join(response.json()["messages"])
    assert "1 new items, 1 updated" in report and "row 4 has no SKU" in report
    assert new.events.first().actor == "Sam"  # shows in History under the importer's name


def test_values_that_dont_fit_go_to_notes_and_reimport_changes_nothing(client):
    csv_text = "SKU,What is it?,Condition,Price\nx-1,Laptop,Shiny,about 20\n"
    _post(client, "a.csv", csv_text.encode())
    item = Item.objects.get(sku_normalized="X-1")
    assert item.condition == "" and item.price is None
    assert "Condition: Shiny" in item.notes and "Price: about 20" in item.notes

    response = _post(client, "a.csv", csv_text.encode())
    assert "Nothing needed changing: all 1 rows" in response.json()["messages"][0]
    assert Item.objects.get(sku_normalized="X-1").notes.count("Condition: Shiny") == 1


def test_dispodex_csv_export_imports_back(client):
    from inventory.services.exports import inventory_csv

    Item.objects.create(sku="r-1", what_is_it="Tablet", price="10.00", condition="Fair", wifi_card_installed=True,
                        date_received="2026-09-01")
    Item.objects.create(sku="other-2", what_is_it="Phone")
    exported = inventory_csv(Item.objects.filter(sku_normalized="R-1"))
    Item.objects.all().delete()

    assert _post(client, "inventory.csv", exported.encode("utf-8")).status_code == 200
    item = Item.objects.get(sku_normalized="R-1")
    assert item.created_at > timezone.now() - timedelta(minutes=1)  # dates and ids from the file aren't copied
    assert (item.what_is_it, str(item.price), item.condition, item.wifi_card_installed) == ("Tablet", "10.00", "Fair", True)
    assert str(item.date_received) == "2026-09-01" and item.notes == ""  # ID/Created/Updated are ignored, not noted


def test_xlsx_json_and_tsv(client, tmp_path):
    from openpyxl import Workbook

    book = Workbook()
    book.active.append(["SKU", "What is it?", "Price", "Date Received"])
    book.active.append(["xl-1", "Printer", 45, __import__("datetime").datetime(2026, 5, 4)])
    book.save(tmp_path / "items.xlsx")
    assert _post(client, "items.xlsx", (tmp_path / "items.xlsx").read_bytes()).status_code == 200
    printer = Item.objects.get(sku_normalized="XL-1")
    assert str(printer.price) == "45.00" and str(printer.date_received) == "2026-05-04"

    body = b'{"items": [{"sku": "js-1", "what_is_it": "Router", "quantity": 2, "wifi_card_installed": true}]}'
    assert _post(client, "items.json", body).status_code == 200
    router = Item.objects.get(sku_normalized="JS-1")
    assert router.quantity == 2 and router.wifi_card_installed is True

    assert _post(client, "items.tsv", b"SKU\tWhat is it?\nts-1\tKeyboard\n").status_code == 200
    assert _post(client, "semi.csv", "SKU;What is it?\nsc-1;Café speaker\n".encode("cp1252")).status_code == 200
    assert Item.objects.get(sku_normalized="SC-1").what_is_it == "Café speaker"


@pytest.mark.parametrize("name, content, message", [
    ("old.xls", b"\xd0\xcf\x11\xe0junk", "Save As"),
    ("nosku.csv", b"Name,Price\nLaptop,5\n", '"SKU" column'),
    ("empty.csv", b"SKU,What is it?\n", "no rows"),
    ("photo.png", b"\x89PNG\r\n", "can't be imported"),
    ("bad.json", b"{not json", "JSON"),
])
def test_unreadable_files_are_refused_without_changes(client, name, content, message):
    response = _post(client, name, content)
    assert response.status_code == 400 and message in response.json()["error"]
    assert backups.list_backups() == []


@pytest.mark.parametrize("address", ["100.112.51.35", "100.64.0.1", "::ffff:192.168.1.20", "10.42.40.111"])
def test_import_allowed_from_shop_network_and_vpn(tmp_path, address):
    """Coworkers reach Dispodex over the shop Wi-Fi or Tailscale (100.64.0.0/10); both may import."""
    Item.objects.create(sku="keep-1")
    snapshot = backups.run_backup().path
    assert _upload(Client(REMOTE_ADDR=address), snapshot).status_code == 200


@pytest.mark.parametrize("address", ["8.8.4.4", "100.128.0.1", "1.1.1.1"])
def test_import_refused_from_the_internet(tmp_path, address):
    path = tmp_path / "x.sqlite3"
    path.write_bytes(b"")
    assert _upload(Client(REMOTE_ADDR=address), path).status_code == 403
