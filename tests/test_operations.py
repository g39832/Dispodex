import base64
import json
import sqlite3
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.core.management import call_command
from django.urls import reverse
from PIL import Image

from archive.models import ArchiveItem
from inventory.models import IntakeDraft, Item, Photo, ScriptCache
from operations import backups, worker
from operations.legacy_import import run_import

pytestmark = pytest.mark.django_db(transaction=True)


def test_backup_verify_and_status(client, item):
    response = client.post(reverse("api_backup"))
    assert response.json()["ok"], response.content
    latest = backups.latest_backup()
    assert latest and latest.with_name(latest.name + ".sha256").exists()
    assert backups.integrity_ok(latest)
    assert client.post(reverse("api_verify")).json()["ok"]
    status = backups.backup_status()
    assert status["count"] == 1 and status["age_hours"] < 1


def test_verify_detects_tampering(item):
    path = backups.snapshot()
    with path.open("ab") as handle:
        handle.write(b"corruption")
    assert backups.verify_latest()["ok"] is False


def test_backup_mirror_and_retention(settings, tmp_path, item):
    settings.PINKSHEET = {**settings.PINKSHEET, "BACKUP_MIRROR_DIR": str(tmp_path / "mirror"), "BACKUP_KEEP": 2}
    for _ in range(3):
        assert backups.run_backup().ok
    assert len(backups.list_backups()) == 2
    assert list((tmp_path / "mirror").glob("pinksheet-*.sqlite3"))


def test_backup_blocked_from_outside_network(item):
    from django.test import Client

    assert Client(REMOTE_ADDR="8.8.4.4").post(reverse("api_backup")).status_code == 403


def test_worker_tick_runs_nightly_backup_once(settings, item):
    settings.PINKSHEET = {**settings.PINKSHEET, "BACKUP_HOUR": 0}
    worker.tick()
    worker.tick()
    assert len(backups.list_backups()) == 1
    assert worker.worker_alive()


def test_backup_command(item, capsys):
    call_command("backup", "--verify")
    assert "Backup complete" in capsys.readouterr().out


# ── QZ Tray signing ──────────────────────────────────────────────────────────
def test_qz_sign_and_certificate(client, settings, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (tmp_path / "private-key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (tmp_path / "digital-certificate.txt").write_text("CERT", encoding="utf-8")
    settings.PINKSHEET = {**settings.PINKSHEET, "QZ_SIGNING_DIR": tmp_path}
    assert client.get(reverse("qz_certificate")).content == b"CERT"
    response = client.post(reverse("qz_sign"), json.dumps({"request": "hello"}), content_type="application/json",
                           HTTP_ORIGIN="http://testserver")
    signature = base64.b64decode(response.content)
    key.public_key().verify(signature, b"hello", padding.PKCS1v15(), hashes.SHA512())  # raises if wrong
    bad = client.post(reverse("qz_sign"), json.dumps({"request": "x"}), content_type="application/json",
                      HTTP_ORIGIN="https://evil.example")
    assert bad.status_code == 403


# ── import from the PHP app ──────────────────────────────────────────────────
def build_legacy_app(root: Path) -> None:
    data = root / "data"
    (data / "sku_photos" / "D-1").mkdir(parents=True)
    Image.new("RGB", (10, 10)).save(data / "sku_photos" / "D-1" / "abc.png")
    con = sqlite3.connect(data / "intake.sqlite")
    con.executescript("""
        CREATE TABLE intake_items (id INTEGER PRIMARY KEY, created_at TEXT, updated_at TEXT, sku TEXT, sku_normalized TEXT,
          status TEXT, what_is_it TEXT, date_received TEXT, functional TEXT, condition TEXT, is_square INTEGER,
          ebay_price REAL, dispotech_price REAL, quantity INTEGER, notes TEXT, reviewed INTEGER, ready INTEGER);
        INSERT INTO intake_items VALUES (1,'2026-01-01 10:00:00','2026-02-01 10:00:00','d-1','D-1','SOLD','Laptop',
          '2026-01-30','unknown','Great',1,123,NULL,2,'hi',0,1);
        INSERT INTO intake_items VALUES (2,'2026-01-01 10:00:00','2026-01-05 10:00:00','d-1','D-1','intake','Older copy',
          'not a date',NULL,NULL,0,NULL,NULL,1,'',0,0);
        INSERT INTO intake_items VALUES (3,'2026-03-01 10:00:00','2026-03-01 10:00:00','x-2','X-2','Tested','Desktop',
          'not a date',NULL,'Unicorn',0,NULL,55,1,'',0,0);
        CREATE TABLE sku_photos (id INTEGER PRIMARY KEY, sku_normalized TEXT, original_name TEXT, stored_name TEXT,
          mime_type TEXT, file_size INTEGER, created_at TEXT, is_thumb INTEGER, sort_order INTEGER);
        INSERT INTO sku_photos VALUES (7,'D-1','front.png','abc.png','image/png',10,'2026-01-01 10:00:00',1,1);
        INSERT INTO sku_photos VALUES (8,'D-1','gone.png','missing.png','image/png',10,'2026-01-01 10:00:00',0,2);
        CREATE TABLE intake_drafts (id INTEGER PRIMARY KEY, sku_normalized TEXT, payload TEXT, version INTEGER, updated_at TEXT);
        INSERT INTO intake_drafts VALUES (1,'D-1','{"notes":"draft"}',3,'2026-02-02T10:00:00+00:00');
        CREATE TABLE script_cache (sku_normalized TEXT, sku_display TEXT, prompt_text TEXT, chatgpt_text TEXT, final_text TEXT, updated_at TEXT);
        INSERT INTO script_cache VALUES ('D-1','D-1','p','c','f','2026-02-02 10:00:00');
        CREATE TABLE archive_items (id INTEGER PRIMARY KEY, created_at TEXT, updated_at TEXT, sku TEXT, sku_normalized TEXT,
          title TEXT, status TEXT, sold_at TEXT, sold_price REAL, purchase_price REAL, source TEXT, buyer TEXT, notes TEXT,
          legacy_source TEXT, legacy_table TEXT, legacy_id TEXT, legacy_payload TEXT, legacy_location_id TEXT, legacy_category_id TEXT);
        INSERT INTO archive_items VALUES (1,'2025-08-29 11:49:19','','IZ-1','IZ-1','Dell','Archived','',NULL,NULL,'','','',
          'Legacy','inventory','41','{}','14','179');
    """)
    con.commit()
    con.close()


def test_import_legacy_copies_everything(tmp_path):
    build_legacy_app(tmp_path)
    summary = run_import(tmp_path)
    assert summary.items == 2 and summary.duplicates_skipped == 1
    item = Item.objects.get(sku_normalized="D-1")
    assert item.what_is_it == "Laptop"  # the newest row wins
    assert item.status == "sold" and item.reviewed == 2
    assert str(item.price) == "123.00" and item.quantity == 2 and item.ready is True
    assert item.functional == "Unknown" and item.condition == "Great"
    other = Item.objects.get(sku_normalized="X-2")
    assert other.status == "ebay draft" and other.date_received is None
    assert other.condition == "Excellent"  # the old top grade maps to the new top grade
    assert "not a date" in other.notes  # unreadable dates are kept in notes, not lost
    photo = Photo.objects.get()
    assert photo.pk == 7 and photo.is_thumb  # ids kept, so old photo links still work
    assert summary.photo_files_missing == 1
    assert IntakeDraft.objects.get().payload == {"notes": "draft"}
    assert ScriptCache.objects.get().final_text == "f"
    assert ArchiveItem.objects.get().legacy_id == "41"


def test_import_refuses_to_overwrite_without_flag(tmp_path, item):
    build_legacy_app(tmp_path)
    with pytest.raises(RuntimeError):
        run_import(tmp_path)
    run_import(tmp_path, replace=True)
    assert not Item.objects.filter(sku_normalized="ABC-1").exists()


def test_import_never_modifies_the_old_database(tmp_path):
    build_legacy_app(tmp_path)
    db = tmp_path / "data" / "intake.sqlite"
    before = db.read_bytes()
    run_import(tmp_path)
    assert db.read_bytes() == before
