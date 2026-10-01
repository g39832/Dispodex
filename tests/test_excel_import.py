"""Importing the old app's "Excel with photos" export (including its broken column names)."""
import io
import zipfile
from pathlib import Path

import pytest
from django.core.management import call_command
from django.urls import reverse
from PIL import Image

from archive.models import ArchiveItem
from inventory.models import Item, ItemEvent, Photo
from inventory.services import photos as photo_service
from operations import excel_import

pytestmark = pytest.mark.django_db(transaction=True)

HEADERS = ["SKU", "Status", "What is it?", "Brand/Model", "Source", "Date Received", "Condition", "Functional",
           "Power On", "Dispotech Price", "eBay Price", "Qty", "eBay Category", "eBay Status", "Where It Goes", "CPU",
           "RAM", "SSD (GB)", "Graphics Card", "Screen Resolution", "Battery Health", "OS", "Compatible OS",
           "WiFi Card Installed", "Picture Taken", "Cords/Adapters", "Keep Items Together", "What Box", "In eBay Room",
           "Is Square", "Care if Square", "Diagnostics Ran", "eBay Category Path", "eBay Category ID", "ID", "Created",
           "Updated", "Photos"]


def old_letter(n: int) -> str:
    """The old exporter's bug: chr(64 + n), so column 27 is '[' instead of 'AA'."""
    return chr(64 + n)


def jpeg(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 240), color).save(buf, "JPEG")
    return buf.getvalue()


def build_export(path: Path, rows: list[dict], photos: dict[int, list[bytes]]) -> Path:
    """rows: header -> value; photos: sheet row number -> list of JPEG bytes."""
    def cell(ref, text):
        text = str(text).replace("&", "&amp;").replace("<", "&lt;")
        return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'
    sheet_rows = ['<row r="1">' + "".join(cell(f"{old_letter(i + 1)}1", h) for i, h in enumerate(HEADERS)) + "</row>"]
    for r, values in enumerate(rows, start=2):
        cells = "".join(cell(f"{old_letter(i + 1)}{r}", values[h]) for i, h in enumerate(HEADERS) if values.get(h) not in (None, ""))
        sheet_rows.append(f'<row r="{r}">{cells}</row>')
    sheet = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
    anchors, rels, media, n = [], [], {}, 0
    for row, images in photos.items():
        for position, data in enumerate(images):
            n += 1
            media[f"xl/media/image{n}.jpg"] = data
            rels.append(f'<Relationship Id="rId{n}" Type="image" Target="../media/image{n}.jpg"/>')
            anchors.append(f'<xdr:oneCellAnchor><xdr:from><xdr:col>8</xdr:col><xdr:colOff>{position * 100000}</xdr:colOff>'
                           f'<xdr:row>{row - 1}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:pic><xdr:blipFill>'
                           f'<a:blip r:embed="rId{n}"/></xdr:blipFill></xdr:pic></xdr:oneCellAnchor>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/drawings/drawing1.xml", f'<xdr:wsDr xmlns:xdr="x" xmlns:a="a" xmlns:r="r">{"".join(anchors)}</xdr:wsDr>')
        z.writestr("xl/drawings/_rels/drawing1.xml.rels", f'<Relationships>{"".join(rels)}</Relationships>')
        for name, data in media.items():
            z.writestr(name, data)
    return path


def row(sku, **extra):
    base = {"SKU": sku, "Status": "intake", "What is it?": "Laptop", "Brand/Model": "Dell Latitude 5590",
            "Condition": "Good", "Functional": "Yes", "Dispotech Price": "120", "eBay Price": "120", "Qty": "1",
            "CPU": "i5-8350U", "RAM": "16gb", "Is Square": "1", "Care if Square": "0", "Diagnostics Ran": "1",
            "WiFi Card Installed": "1", "Created": "2026-07-14 21:50:51", "Updated": "2026-08-04 17:29:39"}
    return {**base, **extra}


@pytest.fixture
def export(tmp_path):
    rows = [
        row("LAP-1"),
        row("LAP-2", Status="SOLD", **{"Brand/Model": "HP " + "x" * 300}),
        row("LAP-3", Status="eBay", Condition="Unicorn", **{"eBay Price": "99.50", "Date Received": "32026-06-06"}),
        row("lap-1", Updated="2026-01-01 00:00:00", **{"What is it?": "Older duplicate"}),
        row("", **{"What is it?": "no sku"}),
        row("LAP-4", Status="Listed", **{"Compatible OS": "None", "Power On": "Maybe"}),
    ]
    return build_export(tmp_path / "export.xlsx", rows, {2: [jpeg((200, 0, 0)), jpeg((0, 200, 0))], 4: [jpeg((0, 0, 200))]})


def test_reads_the_broken_column_names_and_photos(export):
    headers, rows, _ = excel_import.read_workbook(export)
    assert headers == HEADERS  # columns 27+ ('[', '\\', ...) decoded correctly
    assert rows[0].values["Updated"] == "2026-08-04 17:29:39" and len(rows[0].photos) == 2


def test_preview_reports_everything_without_writing(export):
    report, planned, _ = excel_import.plan(export)
    assert report.items == 4 and report.photos == 3 and report.items_with_photos == 2
    assert report.skipped_no_sku == [6]
    assert report.duplicates == [("LAP-1", 2, 5)]  # newest row kept
    assert set(report.missing_columns) >= {"Notes", "Serial Number"}
    assert not Item.objects.exists()


def test_import_keeps_every_value_and_verifies(export, capsys):
    ArchiveItem.objects.create(sku="OLD-1", sku_normalized="OLD-1", title="Kept")
    Item.objects.create(sku="TEST-9", what_is_it="test item")
    call_command("import_excel_export", str(export), "--replace", "--yes")
    out = capsys.readouterr().out
    # Checked against the file first, then the small previews are widened for eBay.
    assert "Verified: every item and every photo matches the file." in out
    assert out.index("Verified") < out.index("Widened 3 small photo(s) to 500px")

    assert set(Item.objects.values_list("sku_normalized", flat=True)) == {"LAP-1", "LAP-2", "LAP-3", "LAP-4"}
    assert ArchiveItem.objects.count() == 1  # the Archive is never replaced
    one = Item.objects.get(sku_normalized="LAP-1")
    assert one.what_is_it == "Laptop" and one.cpu == "i5-8350U" and str(one.price) == "120.00"
    assert one.is_square and not one.care_if_square and one.diagnostics_test_ran and one.wifi_card_installed
    assert one.created_at.isoformat().startswith("2026-07-14T21:50:51")
    sold = Item.objects.get(sku_normalized="LAP-2")
    assert sold.status == "sold" and sold.reviewed == 2
    assert len(sold.brand_model) == 255 and "x" * 290 in sold.notes  # long text kept in full in notes
    odd = Item.objects.get(sku_normalized="LAP-3")
    assert odd.status == "intake" and "Status: eBay" in odd.notes
    assert odd.condition == "Excellent"  # Unicorn -> Excellent
    assert "eBay price: $99.50" in odd.notes and "32026-06-06" in odd.notes and odd.date_received is None
    four = Item.objects.get(sku_normalized="LAP-4")
    assert four.status == "ebay listed" and four.compatible_os == "" and "Power On: Maybe" in four.notes

    photos = list(Photo.objects.filter(sku_normalized="LAP-1").order_by("sort_order"))
    assert len(photos) == 2 and photos[0].is_thumb and not photos[1].is_thumb and all(p.low_res for p in photos)
    assert ItemEvent.objects.filter(action="created").count() == 4
    for photo in Photo.objects.all():
        with Image.open(photo_service.photo_path(photo)) as image:
            assert image.width == 500


def test_refuses_to_mix_without_replace(export):
    Item.objects.create(sku="TEST-9", what_is_it="test item")
    with pytest.raises(Exception, match="--replace"):
        call_command("import_excel_export", str(export), "--yes")
    assert Item.objects.count() == 1


def test_dry_run_changes_nothing(export):
    call_command("import_excel_export", str(export), "--dry-run")
    assert not Item.objects.exists() and not Photo.objects.exists()


def test_not_an_export(tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_text("hello")
    with pytest.raises(Exception, match="not an Excel"):
        call_command("import_excel_export", str(bad), "--dry-run")


def test_low_res_photos_are_flagged_in_the_app(client, export):
    call_command("import_excel_export", str(export), "--replace", "--yes")
    photos = client.get(reverse("api_photo_list") + "?sku=LAP-1").json()["photos"]
    assert all(p["low_res"] for p in photos)
    html = client.get(reverse("lookup") + "?gap=low-res").content.decode()
    assert "LAP-1" in html and "LAP-3" in html and "LAP-2" not in html
    assert "Photos:</span> low-res, to retake" in html


def test_board_shows_every_unsold_item_and_recent_sold(client, settings):
    from inventory.views import api
    Item.objects.bulk_create([Item(sku=f"A-{i}", sku_normalized=f"A-{i}", what_is_it="x") for i in range(1100)])
    Item.objects.bulk_create([Item(sku=f"S-{i}", sku_normalized=f"S-{i}", what_is_it="x", status="sold")
                              for i in range(api.SOLD_ON_BOARD + 20)])
    data = client.get(reverse("api_board_cards")).json()
    assert len(data["lanes"]["intake"]) == 1100  # the old 1,000-item cap is gone
    assert len(data["lanes"]["sold"]) == api.SOLD_ON_BOARD and data["more"]["sold"] == 20


def test_item_page_shows_low_res_badge(client, export):
    call_command("import_excel_export", str(export), "--replace", "--yes")
    html = client.get(reverse("intake") + "?sku=LAP-1").content.decode()
    assert html.count('class="badge-lowres"') == 2
