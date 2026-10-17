"""Scrap: take things in (date, who, weight), move them to the scrap side, totals, import and export."""
import csv
import io
from datetime import timedelta
from decimal import Decimal

import pytest
import requests
import responses as responses_lib
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from scrap import importer, scale
from scrap.models import ScrapCategory, ScrapEntry

pytestmark = pytest.mark.django_db


@pytest.fixture
def named(client):
    client.cookies["ps_name"] = "Grayson"
    return client


def weigh(client, **data):
    return client.post("/scrap/weigh/", {"weight": "12.5", "drop_off": "public", **data}, follow=True)


def text(response) -> str:
    return response.content.decode()


# ── taking scrap in ──────────────────────────────────────────────────────────
def test_take_in_records_date_person_and_weight(named):
    response = weigh(named, description="Box of cables", category="Cables", note="from the back room")
    assert response.status_code == 200
    entry = ScrapEntry.objects.get()
    assert entry.weight_lbs == Decimal("12.50")
    assert entry.weighed_by == "Grayson" and entry.added_by == "Grayson"
    assert timezone.localtime(entry.weighed_at).date() == timezone.localdate()
    assert entry.moved_at is None and entry.source == ScrapEntry.Source.TYPED
    assert entry.category.name == "Cables" and ScrapCategory.objects.count() == 1
    assert "taken in by Grayson" in text(response)


def test_take_in_for_someone_else_on_an_earlier_day(named):
    day = timezone.localdate() - timedelta(days=3)
    weigh(named, weighed_by="Iain", taken_in=day.isoformat())
    entry = ScrapEntry.objects.get()
    assert entry.weighed_by == "Iain"
    assert entry.added_by == "Grayson"  # who typed it in is still kept
    local = timezone.localtime(entry.weighed_at)
    assert local.date() == day and local.hour == 12


def test_take_in_rejects_bad_weight_and_future_date(named):
    assert "Enter the weight" in text(weigh(named, weight="heavy"))
    assert "Enter the weight" in text(weigh(named, weight="0"))
    assert "Enter the weight" in text(weigh(named, weight="-4"))
    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
    assert "Pick the date" in text(weigh(named, taken_in=tomorrow))
    assert "Pick the date" in text(weigh(named, taken_in="not a date"))
    assert not ScrapEntry.objects.exists()


def test_without_a_saved_name_taken_in_by_is_required(client):
    assert "Add your name" in text(weigh(client))
    assert not ScrapEntry.objects.exists()
    weigh(client, weighed_by="Zech")
    entry = ScrapEntry.objects.get()
    assert entry.weighed_by == "Zech" and entry.added_by == "Zech"


def test_moved_straight_away(named):
    weigh(named, moved_now="1")
    entry = ScrapEntry.objects.get()
    assert entry.moved_at is not None and entry.moved_by == "Grayson"


def test_category_reuses_existing_spelling(named):
    ScrapCategory.objects.create(name="Laptops")
    weigh(named, category="  laptops ")
    assert ScrapCategory.objects.count() == 1
    assert ScrapEntry.objects.get().category.name == "Laptops"


# ── moving to scrap ──────────────────────────────────────────────────────────
def make(lbs, by="Grayson", days_ago=0, moved=False, **extra):
    when = timezone.now() - timedelta(days=days_ago)
    return ScrapEntry.objects.create(
        weight_lbs=Decimal(lbs), weighed_by=by, weighed_at=when,
        moved_at=when if moved else None, moved_by=by if moved else "", **extra,
    )


def test_mark_ticked_entries_moved(named):
    a, b, c = make("10"), make("5.25"), make("3")
    response = named.post("/scrap/move/", {"entry": [a.pk, b.pk]}, follow=True)
    assert "Moved 2 items to scrap (15.25 lbs)" in text(response)
    a.refresh_from_db(); c.refresh_from_db()
    assert a.moved_by == "Grayson" and a.moved_at and c.moved_at is None


def test_row_button_moves_only_that_row(named):
    a, b = make("10"), make("5")
    named.post("/scrap/move/", {"entry": [a.pk, b.pk], "only": b.pk})
    a.refresh_from_db(); b.refresh_from_db()
    assert a.moved_at is None and b.moved_at is not None


def test_move_needs_a_name(client):
    a = make("10")
    assert "Add your name" in text(client.post("/scrap/move/", {"entry": a.pk}, follow=True))
    client.post("/scrap/move/", {"entry": a.pk, "name": "Iain"})
    a.refresh_from_db()
    assert a.moved_by == "Iain"


def test_already_moved_entries_are_not_moved_again(named):
    a = make("10", by="Iain", moved=True)
    first = a.moved_at
    named.post("/scrap/move/", {"entry": a.pk})
    a.refresh_from_db()
    assert a.moved_at == first and a.moved_by == "Iain"


# ── the page and its totals ─────────────────────────────────────────────────
def test_page_totals_by_period(named):
    make("10")                                   # waiting
    make("4.5", moved=True, by="Iain")           # moved today
    make("100", moved=True, days_ago=400)        # moved over a year ago
    page = named.get("/scrap/?range=today")
    assert page.status_code == 200
    ctx = page.context
    assert ctx["waiting_totals"] == {"lbs": Decimal("10.00"), "count": 1}
    assert ctx["moved_totals"]["lbs"] == Decimal("4.50")
    assert ctx["weighed_totals"]["lbs"] == Decimal("14.50")
    assert ctx["all_moved_totals"]["lbs"] == Decimal("104.50")
    assert named.get("/scrap/?range=all").context["moved_totals"]["lbs"] == Decimal("104.50")
    people = {p["name"]: p for p in ctx["by_person"]}
    assert people["Iain"]["moved"] == Decimal("4.50") and people["Grayson"]["weighed"] == Decimal("10.00")


def test_custom_date_range_and_filters(named):
    cables = ScrapCategory.objects.create(name="Cables")
    make("7", days_ago=10, category=cables, description="cords")
    make("9", days_ago=10, by="Iain")
    day = (timezone.localdate() - timedelta(days=10)).isoformat()
    page = named.get(f"/scrap/?from={day}&to={day}")
    assert page.context["range_key"] == "custom" and page.context["weighed_totals"]["count"] == 2
    assert page.context["page_obj"].paginator.count == 2
    assert named.get(f"/scrap/?from={day}&to={day}&category={cables.pk}").context["page_obj"].paginator.count == 1
    assert named.get(f"/scrap/?from={day}&to={day}&person=Iain").context["page_obj"].paginator.count == 1
    assert named.get(f"/scrap/?from={day}&to={day}&q=cords").context["page_obj"].paginator.count == 1
    assert named.get("/scrap/?range=today").context["page_obj"].paginator.count == 0


def test_empty_page_renders(client):
    page = client.get("/scrap/")
    assert page.status_code == 200
    assert "Nothing weighed in yet" in text(page)


# ── editing ──────────────────────────────────────────────────────────────────
def test_edit_entry(named):
    entry = make("10", by="Iain")
    day = (timezone.localdate() - timedelta(days=2)).isoformat()
    response = named.post(f"/scrap/{entry.pk}/", {
        "weight": "11.2", "taken_in": day, "weighed_by": "Zech", "description": "Monitors", "category": "Screens",
        "note": "",
    }, follow=True)
    assert "Changes saved" in text(response)
    entry.refresh_from_db()
    assert entry.weight_lbs == Decimal("11.20") and entry.weighed_by == "Zech"
    assert timezone.localtime(entry.weighed_at).date().isoformat() == day
    assert entry.category.name == "Screens" and entry.edited_by == "Grayson"


def test_edit_keeps_time_when_date_unchanged(named):
    entry = make("10")
    before = entry.weighed_at
    named.post(f"/scrap/{entry.pk}/", {"weight": "10", "taken_in": timezone.localtime(before).date().isoformat(),
                                       "weighed_by": "Grayson"})
    entry.refresh_from_db()
    assert entry.weighed_at == before


def test_put_back_to_waiting(named):
    entry = make("10", moved=True)
    named.post(f"/scrap/{entry.pk}/unmove/")
    entry.refresh_from_db()
    assert entry.moved_at is None and entry.moved_by == "" and entry.edited_by == "Grayson"


def test_delete(named):
    entry = make("10")
    named.post(f"/scrap/{entry.pk}/delete/")
    assert not ScrapEntry.objects.exists()


def test_delete_is_staff_only_when_sign_in_is_on(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "REQUIRE_LOGIN": True}
    User.objects.create_user("worker", password="pw")
    User.objects.create_user("boss", password="pw", is_staff=True)
    entry = make("10")
    client.login(username="worker", password="pw")
    client.post(f"/scrap/{entry.pk}/delete/")
    assert ScrapEntry.objects.exists()
    client.login(username="boss", password="pw")
    client.post(f"/scrap/{entry.pk}/delete/")
    assert not ScrapEntry.objects.exists()


def test_page_needs_sign_in_when_turned_on(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "REQUIRE_LOGIN": True}
    assert client.get("/scrap/").status_code == 302


# ── categories ───────────────────────────────────────────────────────────────
def test_categories_add_rename_hide(named):
    named.post("/scrap/categories/", {"action": "add", "name": "Laptops"})
    category = ScrapCategory.objects.get()
    assert category.created_by == "Grayson"
    named.post("/scrap/categories/", {"action": "add", "name": "laptops"})
    assert ScrapCategory.objects.count() == 1
    named.post("/scrap/categories/", {"action": "rename", "id": category.pk, "name": "Notebooks"})
    category.refresh_from_db()
    assert category.name == "Notebooks"
    named.post("/scrap/categories/", {"action": "hide", "id": category.pk})
    category.refresh_from_db()
    assert category.hidden
    page = named.get("/scrap/")
    assert list(page.context["categories"]) == []
    assert named.get("/scrap/categories/").status_code == 200


def test_rename_cannot_clash(named):
    a = ScrapCategory.objects.create(name="Laptops")
    ScrapCategory.objects.create(name="Cables")
    response = named.post("/scrap/categories/", {"action": "rename", "id": a.pk, "name": "cables"}, follow=True)
    assert "already a category" in text(response)
    a.refresh_from_db()
    assert a.name == "Laptops"


# ── import ───────────────────────────────────────────────────────────────────
GOOGLE_CSV = (
    "Timestamp,Name,What,Weight (lbs),Category,Notes\n"
    "10/1/2026 9:15:02,Iain,Cables,12.5,Cables,\n"
    "10/2/2026 14:03:00,Zech,Laptops,\"1,204 lbs\",Laptops,big load\n"
    "10/3/2026,Iain,Mixed,4 kg,,\n"
    "10/4/2026,Iain,Nothing,,,\n"
    "10/5/2026,Iain,Cables,12.5,Cables,\n"
)


def upload(client, content=GOOGLE_CSV, name="Scrap log - Sheet1.csv"):
    return client.post("/scrap/import/", {"action": "upload", "file": SimpleUploadedFile(name, content.encode())}, follow=True)


def test_parse_weight():
    assert importer.parse_weight("12.5") == Decimal("12.50")
    assert importer.parse_weight("1,204 lbs") == Decimal("1204.00")
    assert importer.parse_weight("4 kg") == Decimal("8.82")
    assert importer.parse_weight("8 oz") == Decimal("0.50")
    assert importer.parse_weight("") is None
    assert importer.parse_weight("n/a") is None
    assert importer.parse_weight("0") is None


def test_guess_mapping():
    headers = ["Timestamp", "Name", "What", "Weight (lbs)", "Category", "Notes"]
    assert importer.guess_mapping(headers) == {
        "date": "Timestamp", "weighed_by": "Name", "description": "What", "weight": "Weight (lbs)",
        "category": "Category", "note": "Notes",
    }
    assert importer.guess_mapping(["Gross weight of the item", "Who"])["weight"] == "Gross weight of the item"


def test_import_preview_then_import(named):
    preview = upload(named)
    assert preview.status_code == 200
    result = preview.context["result"]
    assert len(result.new_rows) == 4 and len(result.skipped) == 1 and "row 5" in result.skipped[0]
    assert result.new_lbs == Decimal("1237.82")
    assert result.new_categories == ["Cables", "Laptops"]
    assert not ScrapEntry.objects.exists()  # nothing saved until Import is pressed

    done = named.post("/scrap/import/", {
        "action": "import", "moved_default": "1", **{f"map_{k}": v for k, v in importer.guess_mapping(
            ["Timestamp", "Name", "What", "Weight (lbs)", "Category", "Notes"]).items()},
    }, follow=True)
    assert "Imported 4 rows" in text(done)
    assert ScrapEntry.objects.count() == 4
    zech = ScrapEntry.objects.get(weighed_by="Zech")
    assert zech.weight_lbs == Decimal("1204.00") and zech.note == "big load" and zech.category.name == "Laptops"
    local = timezone.localtime(zech.weighed_at)
    assert (local.month, local.day, local.hour, local.minute) == (10, 2, 14, 3)
    assert zech.moved_at == zech.weighed_at and zech.moved_by == "Zech"
    assert zech.source == ScrapEntry.Source.IMPORT and zech.added_by == "Grayson"
    # Two identical "Cables 12.5" rows on different days are two entries.
    assert ScrapEntry.objects.filter(description="Cables").count() == 2


def test_reimport_only_adds_new_rows(named):
    mapping = {f"map_{k}": v for k, v in importer.guess_mapping(GOOGLE_CSV.splitlines()[0].split(",")).items()}
    upload(named)
    named.post("/scrap/import/", {"action": "import", "moved_default": "1", **mapping})
    assert ScrapEntry.objects.count() == 4
    more = GOOGLE_CSV + "10/6/2026,Zech,Board,3.3,,\n"
    preview = upload(named, more)
    assert preview.context["result"].duplicates == 4 and len(preview.context["result"].new_rows) == 1
    named.post("/scrap/import/", {"action": "import", "moved_default": "1", **mapping})
    assert ScrapEntry.objects.count() == 5


def test_import_as_waiting_and_with_moved_column(named):
    upload(named, "Date,Weight\n10/1/2026,5\n")
    named.post("/scrap/import/", {"action": "import", "moved_default": "0", "map_date": "Date", "map_weight": "Weight"})
    assert ScrapEntry.objects.get().moved_at is None

    upload(named, "Date,Weight,Moved\n10/1/2026,6,yes\n10/1/2026,7,no\n10/1/2026,8,10/9/2026\n")
    named.post("/scrap/import/", {"action": "import", "map_date": "Date", "map_weight": "Weight", "map_moved": "Moved"})
    six, seven, eight = (ScrapEntry.objects.get(weight_lbs=w) for w in (6, 7, 8))
    assert six.moved_at is not None and seven.moved_at is None
    assert timezone.localtime(eight.moved_at).day == 9


def test_import_changing_columns_updates_preview(named):
    upload(named, "Day,Lbs,Other\n10/1/2026,5,99\n")
    response = named.post("/scrap/import/", {"map_date": "Day", "map_weight": "Other", "moved_default": "1"}, follow=True)
    assert response.context["result"].new_lbs == Decimal("99.00")


def test_import_errors(named):
    assert "column names but no rows" in text(upload(named, "Date,Weight\n"))
    assert "Pick a CSV file" in text(upload(named, "x", name="scrap.pdf"))
    upload(named, "Date,Thing\n10/1/2026,abc\n")
    page = named.get("/scrap/import/")
    assert "Pick which column has the weight" in text(page)
    named.post("/scrap/import/", {"action": "cancel"})
    assert "scrap_import" not in named.session


def test_import_bad_date_is_left_out(named):
    upload(named, "Date,Weight\nsometime,5\n10/1/2026,6\n")
    result = named.get("/scrap/import/").context["result"]
    assert len(result.new_rows) == 1 and "wasn't understood" in result.skipped[0]


# ── export ───────────────────────────────────────────────────────────────────
def test_export_csv(named):
    make("10", by="Iain", description="Cables", moved=True)
    make("2.5", by="Zech")
    response = named.get("/exports/scrap.csv?range=all")
    assert response["Content-Type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[0][:3] == ["Date taken in", "Taken in by", "Weight (lbs)"]
    assert {(r[1], r[2]) for r in rows[1:]} == {("Iain", "10.00"), ("Zech", "2.50")}
    only_waiting = named.get("/exports/scrap.csv?range=all&state=waiting")
    assert len(list(csv.reader(io.StringIO(only_waiting.content.decode("utf-8-sig"))))) == 2


# ── demo, credit ─────────────────────────────────────────────────────────────
def test_demo_mode_blocks_changes(named, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "DEMO_MODE": True}
    weigh(named)
    assert not ScrapEntry.objects.exists()
    entry = make("1")
    named.post("/scrap/move/", {"entry": entry.pk})
    entry.refresh_from_db()
    assert entry.moved_at is None
    assert named.get("/scrap/").status_code == 200


def test_github_credit_in_sidebar_and_system_page(client):
    page = text(client.get("/scrap/"))
    assert 'href="https://github.com/g39832"' in page and "Built by g39832" in page
    system = text(client.get("/system/"))
    assert "About Dispodex" in system and "https://github.com/g39832/Dispodex" in system


def test_lbs_filter():
    from core.templatetags.ui import lbs
    assert lbs(Decimal("1204.50")) == "1,204.5"
    assert lbs(Decimal("1200.00")) == "1,200"
    assert lbs(Decimal("0.25")) == "0.25"
    assert lbs(None) == "0"


# ── live scale ───────────────────────────────────────────────────────────────
SCALE = "http://scale.test:8765/api/weight"


@pytest.fixture
def scale_url(settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "SCALE_URL": SCALE}


@responses_lib.activate
def test_scale_reading_in_pounds(client, scale_url):
    responses_lib.get(SCALE, json={"online": True, "stable": True, "weight": 127.0, "unit": "lb", "raw": "ST,GS,+ 127lb"})
    data = client.get("/api/scrap/scale/").json()
    assert data == {"ok": True, "configured": True, "online": True, "stable": True, "lbs": "127.00", "error": ""}


@responses_lib.activate
def test_scale_converts_units_and_reports_settling(client, scale_url):
    responses_lib.get(SCALE, json={"online": True, "stable": False, "weight": 10, "unit": "kg"})
    data = client.get("/api/scrap/scale/").json()
    assert data["lbs"] == "22.05" and data["stable"] is False


@responses_lib.activate
def test_scale_offline_or_unreachable(client, scale_url):
    responses_lib.get(SCALE, json={"online": False})
    assert client.get("/api/scrap/scale/").json()["online"] is False
    responses_lib.replace(responses_lib.GET, SCALE, body=requests.ConnectionError("down"))
    data = client.get("/api/scrap/scale/").json()
    assert data["online"] is False and "Can't reach the scale" in data["error"]
    responses_lib.replace(responses_lib.GET, SCALE, json={"online": True, "weight": "junk", "unit": "lb"})
    assert client.get("/api/scrap/scale/").json()["lbs"] is None


def test_scale_not_set_up(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "SCALE_URL": ""}
    data = client.get("/api/scrap/scale/").json()
    assert data["configured"] is False and data["lbs"] is None
    page = text(client.get("/scrap/"))
    assert "No scale set up yet" in page and "data-scale-get" not in page


def test_scale_readout_on_page_and_scale_source_saved(named, scale_url):
    assert "data-scale-get" in text(named.get("/scrap/"))
    weigh(named, weight="127", source="scale")
    assert ScrapEntry.objects.get().source == ScrapEntry.Source.SCALE


# ── drop-off: public or company ─────────────────────────────────────────────
def test_drop_off_is_required(named):
    response = named.post("/scrap/weigh/", {"weight": "5"}, follow=True)
    assert "public drop-off or a company drop-off" in text(response)
    assert "public drop-off or a company drop-off" in text(weigh(named, drop_off="maybe"))
    assert not ScrapEntry.objects.exists()


def test_company_drop_off_keeps_company_name(named):
    response = weigh(named, drop_off="company", company="  Acme   Corp ")
    entry = ScrapEntry.objects.get()
    assert entry.drop_off == "company" and entry.company == "Acme Corp"
    assert "company drop-off, Acme Corp" in text(response)


def test_public_drop_off_ignores_company_name(named):
    weigh(named, drop_off="public", company="Acme")
    entry = ScrapEntry.objects.get()
    assert entry.drop_off == "public" and entry.company == ""


def test_drop_off_totals_filter_and_export(named):
    make("10", drop_off="public")
    make("30", drop_off="company", company="Acme")
    make("5")  # from before drop-off was asked
    page = named.get("/scrap/?range=all")
    rows = {row["key"]: row for row in page.context["by_drop_off"]}
    assert rows["public"]["lbs"] == Decimal("10.00") and rows["company"]["lbs"] == Decimal("30.00")
    assert rows["unknown"]["name"] == "Not recorded"
    assert named.get("/scrap/?range=all&drop_off=company").context["page_obj"].paginator.count == 1
    assert named.get("/scrap/?range=all&drop_off=unknown").context["page_obj"].paginator.count == 1
    assert named.get("/scrap/?range=all&q=acme").context["page_obj"].paginator.count == 1
    exported = list(csv.reader(io.StringIO(named.get("/exports/scrap.csv?range=all").content.decode("utf-8-sig"))))
    assert exported[0][3:5] == ["Drop-off", "Company"]
    assert ["Company drop-off", "Acme"] in [row[3:5] for row in exported[1:]]


def test_edit_drop_off(named):
    entry = make("10", drop_off="public")
    named.post(f"/scrap/{entry.pk}/", {"weight": "10", "weighed_by": "Grayson", "drop_off": "company", "company": "Acme",
                                       "taken_in": timezone.localtime(entry.weighed_at).date().isoformat()})
    entry.refresh_from_db()
    assert entry.drop_off == "company" and entry.company == "Acme"


def test_import_drop_off_column(named):
    upload(named, "Date,Weight,Drop off,Company\n10/1/2026,5,Public,\n10/1/2026,6,Company drop off,Acme\n10/1/2026,7,,\n")
    named.post("/scrap/import/", {"action": "import", "moved_default": "1", "map_date": "Date", "map_weight": "Weight",
                                  "map_drop_off": "Drop off", "map_company": "Company"})
    by_weight = {int(e.weight_lbs): e for e in ScrapEntry.objects.all()}
    assert by_weight[5].drop_off == "public" and by_weight[6].drop_off == "company"
    assert by_weight[6].company == "Acme" and by_weight[7].drop_off == ""


def test_import_fingerprint_ignores_new_columns(named):
    """Rows imported before drop-off existed must still be spotted as already imported."""
    upload(named, "Date,Weight\n10/1/2026,5\n")
    named.post("/scrap/import/", {"action": "import", "moved_default": "1", "map_date": "Date", "map_weight": "Weight"})
    upload(named, "Date,Weight,Drop off\n10/1/2026,5,\n")
    assert named.get("/scrap/import/").context["result"].duplicates == 1


def test_drop_off_from_text():
    assert importer.drop_off_from("Public") == "public"
    assert importer.drop_off_from("walk-in") == "public"
    assert importer.drop_off_from("Business pickup") == "company"
    assert importer.drop_off_from("Acme LLC") == "company"
    assert importer.drop_off_from("since monday") == ""
    assert importer.drop_off_from("") == ""


# ── scale settings ───────────────────────────────────────────────────────────
@responses_lib.activate
def test_scale_address_set_in_the_app(named, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "SCALE_URL": ""}
    responses_lib.get("http://10.0.0.20:8765/api/weight", json={"online": True, "stable": True, "weight": 42, "unit": "lb"})
    response = named.post("/scrap/scale/", {"url": " 10.0.0.20:8765 "}, follow=True)
    assert "Scale address saved" in text(response)
    assert "42 lbs" in text(response) and "Connected" in text(response)
    assert scale.url() == "http://10.0.0.20:8765/api/weight"
    assert "data-scale-get" in text(named.get("/scrap/"))
    assert named.get("/api/scrap/scale/").json()["lbs"] == "42.00"


def test_scale_address_in_app_wins_over_env(named, scale_url):
    named.post("/scrap/scale/", {"url": "http://pi.local:9000/api/weight"})
    assert scale.url() == "http://pi.local:9000/api/weight"
    named.post("/scrap/scale/", {"url": ""})
    assert scale.url() == SCALE


def test_scale_address_rejects_junk(named):
    assert "Enter the scale" in text(named.post("/scrap/scale/", {"url": "ftp://x"}, follow=True))
    assert "Enter the scale" in text(named.post("/scrap/scale/", {"url": "http://"}, follow=True))


@responses_lib.activate
def test_scale_settings_shows_why_it_fails(named, scale_url):
    responses_lib.get(SCALE, body=requests.ConnectionError("down"))
    page = text(named.get("/scrap/scale/"))
    assert "Not working" in page and "Can&#x27;t reach the scale" in page


def test_only_staff_set_the_scale_when_sign_in_is_on(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "REQUIRE_LOGIN": True, "SCALE_URL": ""}
    User.objects.create_user("worker", password="pw")
    User.objects.create_user("boss", password="pw", is_staff=True)
    client.login(username="worker", password="pw")
    client.post("/scrap/scale/", {"url": "10.0.0.20:8765"})
    assert scale.url() == ""
    assert "Only a staff member" in text(client.get("/scrap/scale/"))
    client.login(username="boss", password="pw")
    client.post("/scrap/scale/", {"url": "10.0.0.20:8765"})
    assert scale.url() == "http://10.0.0.20:8765/api/weight"


def test_tab_is_called_weigh_in(client):
    page = text(client.get("/scrap/"))
    assert "<title>Weigh-in" in page and 'class="page-title">Weigh-in<' in page
    assert ">Scrap<" not in page
