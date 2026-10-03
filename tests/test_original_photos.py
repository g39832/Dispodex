"""Swapping the old Excel export's low-res previews for the old app's original photos."""
import sqlite3
from pathlib import Path

import pytest
from django.core.management import call_command
from PIL import Image

from inventory.models import Item, ItemEvent, Photo
from inventory.services import photos as photo_service
from operations import original_photos

pytestmark = pytest.mark.django_db(transaction=True)


def old_app(root: Path, photos: list[tuple]) -> Path:
    """An old app folder with ``(sku, stored_name, size, is_thumb, sort_order)`` photos."""
    data = root / "data"
    data.mkdir(parents=True)
    con = sqlite3.connect(data / "intake.sqlite")
    con.execute("CREATE TABLE sku_photos (id INTEGER PRIMARY KEY, sku_normalized TEXT, original_name TEXT, "
                "stored_name TEXT, mime_type TEXT, file_size INTEGER, created_at TEXT, is_thumb INTEGER, sort_order INTEGER)")
    for sku, stored, size, is_thumb, order in photos:
        if size:
            (data / "sku_photos" / sku).mkdir(parents=True, exist_ok=True)
            Image.new("RGB", size, (200, 60, 60)).save(data / "sku_photos" / sku / stored)
        con.execute("INSERT INTO sku_photos (sku_normalized, original_name, stored_name, mime_type, is_thumb, sort_order) "
                    "VALUES (?, ?, ?, 'image/png', ?, ?)", (sku, f"orig-{stored}", stored, is_thumb, order))
    con.commit()
    con.close()
    return root


def low_res(sku: str, order: int, *, thumb=False, size=(320, 240)) -> Photo:
    path = photo_service.photo_root() / sku / f"preview{order}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size).save(path)
    return Photo.objects.create(sku_normalized=sku, original_name=path.name, stored_name=path.name,
                                mime_type="image/png", file_size=path.stat().st_size,
                                sort_order=order, is_thumb=thumb, low_res=True)


def test_low_res_previews_are_replaced_by_the_originals(tmp_path):
    Item.objects.create(sku="A-1", what_is_it="Laptop")
    first, second = low_res("A-1", 1), low_res("A-1", 2, thumb=True)
    staff = Photo.objects.create(sku_normalized="A-1", original_name="new.png", stored_name="new.png",
                                 mime_type="image/png", file_size=1, sort_order=3)
    root = old_app(tmp_path / "old", [("A-1", "a.png", (1200, 900), 0, 1), ("A-1", "b.png", (1200, 900), 1, 2)])

    result = original_photos.run(root, actor="Grayson")

    assert len(result.skus) == 1 and not result.failed
    assert not Photo.objects.filter(pk__in=[first.pk, second.pk]).exists()
    assert Photo.objects.filter(pk=staff.pk).exists()  # photos taken in Dispodex are kept
    originals = list(Photo.objects.filter(sku_normalized="A-1", low_res=False).exclude(pk=staff.pk).order_by("sort_order"))
    assert [p.original_name for p in originals] == ["orig-a.png", "orig-b.png"]
    assert [p.sort_order for p in originals] == [1, 2]
    assert [p.is_thumb for p in originals] == [False, True]
    for photo in originals:
        with Image.open(photo_service.photo_path(photo)) as stored:
            assert stored.size == (1200, 900)
    # The preview files stay, so restoring an older backup still finds them.
    assert (photo_service.photo_root() / "A-1" / "preview1.png").exists()
    event = ItemEvent.objects.get(sku_normalized="A-1", action=ItemEvent.Action.PHOTO_ADDED)
    assert event.actor == "Grayson" and "2 low-res" in event.note


def test_items_without_bigger_originals_are_left_alone(tmp_path):
    keep = low_res("B-1", 1)
    also_keep = low_res("C-1", 1)
    root = old_app(tmp_path / "old", [("C-1", "tiny.png", (300, 200), 0, 1), ("D-1", "gone.png", None, 0, 1)])
    result = original_photos.run(root)
    assert result.skus == [] and result.no_originals == ["B-1"] and result.not_sharper == ["C-1"]
    assert Photo.objects.filter(pk__in=[keep.pk, also_keep.pk], low_res=True).count() == 2


def test_extra_originals_go_after_existing_photos(tmp_path):
    low_res("E-1", 1, thumb=True)
    Photo.objects.create(sku_normalized="E-1", original_name="n.png", stored_name="n.png",
                         mime_type="image/png", file_size=1, sort_order=5)
    root = old_app(tmp_path / "old", [("E-1", "a.png", (900, 900), 1, 1), ("E-1", "b.png", (900, 900), 0, 2)])
    original_photos.run(root)
    orders = dict(Photo.objects.filter(sku_normalized="E-1").values_list("original_name", "sort_order"))
    assert orders == {"n.png": 5, "orig-a.png": 1, "orig-b.png": 6}
    assert Photo.objects.get(sku_normalized="E-1", is_thumb=True).original_name == "orig-a.png"


def test_running_twice_changes_nothing_the_second_time(tmp_path):
    low_res("F-1", 1)
    root = old_app(tmp_path / "old", [("F-1", "a.png", (1000, 800), 0, 1)])
    original_photos.run(root)
    assert original_photos.run(root).skus == []
    assert Photo.objects.filter(sku_normalized="F-1").count() == 1


def test_the_old_app_is_never_changed(tmp_path):
    low_res("G-1", 1)
    root = old_app(tmp_path / "old", [("G-1", "a.png", (1000, 800), 0, 1)])
    files = {p: p.read_bytes() for p in (root / "data").rglob("*") if p.is_file()}
    original_photos.run(root)
    assert {p: p.read_bytes() for p in (root / "data").rglob("*") if p.is_file()} == files


def test_command_dry_run_changes_nothing(tmp_path, capsys):
    low_res("H-1", 1)
    root = old_app(tmp_path / "old", [("H-1", "a.png", (1000, 800), 0, 1)])
    call_command("restore_original_photos", str(root), "--dry-run")
    assert "Items that will get their original photos: 1" in capsys.readouterr().out
    assert Photo.objects.get(sku_normalized="H-1").low_res


def test_command_backs_up_then_replaces(tmp_path, capsys):
    low_res("J-1", 1)
    root = old_app(tmp_path / "old", [("J-1", "a.png", (1000, 800), 0, 1)])
    call_command("restore_original_photos", str(root), "--yes")
    out = capsys.readouterr().out
    assert "Backed up first" in out and "Gave 1 item(s) their original photos." in out
    assert not Photo.objects.get(sku_normalized="J-1").low_res
