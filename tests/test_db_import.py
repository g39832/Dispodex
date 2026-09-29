"""The System page's "Import database" button."""
import shutil
import sqlite3

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

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
