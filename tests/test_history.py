"""Item history: every change is recorded with who made it."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.actor import NAME_COOKIE, clean_name
from inventory.models import Item, ItemEvent
from tests.test_square import order_event, signed_post

pytestmark = pytest.mark.django_db(transaction=True)


def intake_post(client, **fields):
    data = {"sku": "HIST-1", "what_is_it": "Laptop", "status": "intake", "quantity": "1", **fields}
    return client.post(reverse("intake"), data)


def quick(client, sku, field, value):
    return client.post(reverse("api_item_update", args=[sku]), {"field": field, "value": value},
                       content_type="application/json")


def events(sku="HIST-1"):
    return list(ItemEvent.objects.filter(sku_normalized=sku).order_by("created_at", "id"))


def test_create_then_edit_records_readable_changes(client):
    client.cookies[NAME_COOKIE] = "Jordan"
    intake_post(client, price="200")
    intake_post(client, price="249.99", cpu="i5-8350U")
    created, edited = events()
    assert created.action == "created" and created.actor == "Jordan"
    assert edited.action == "edited"
    assert {c["label"]: (c["old"], c["new"]) for c in edited.changes} == {
        "Price": ("$200.00", "$249.99"),
        "CPU": ("", "i5-8350U"),
    }


def test_saving_without_changes_adds_nothing(client):
    intake_post(client)
    intake_post(client)
    assert [e.action for e in events()] == ["created"]


def test_unnamed_device_is_labelled_by_address(client):
    intake_post(client)
    assert events()[0].actor == "Device 127.0.0.1"


def test_signed_in_user_wins_over_device_name(client, django_user_model):
    user = django_user_model.objects.create_user("sam", password="x" * 12, first_name="Sam", last_name="Lee")
    client.force_login(user)
    client.cookies[NAME_COOKIE] = "Someone else"
    intake_post(client)
    assert events()[0].actor == "Sam Lee"


def test_device_names_are_cleaned():
    assert clean_name("  <b>Ann</b>\n ") == "bAnn/b"
    assert clean_name("Sam%20Lee") == "Sam Lee"
    assert len(clean_name("x" * 200)) == 40


def test_quick_edits_by_same_person_merge(client):
    intake_post(client)
    for text in ("Scr", "Scratch", "Scratch on lid"):
        quick(client, "HIST-1", "notes", text)
    created, notes = events()
    assert notes.changes == [{"field": "notes", "label": "Notes", "old": "", "new": "Scratch on lid"}]


def test_edit_and_undo_within_window_leaves_no_entry(client):
    intake_post(client, price="10")
    quick(client, "HIST-1", "price", "12")
    quick(client, "HIST-1", "price", "10")
    assert [e.action for e in events()] == ["created"]


def test_old_or_other_person_edits_are_not_merged(client):
    intake_post(client)
    quick(client, "HIST-1", "status", "ebay draft")
    ItemEvent.objects.filter(action="edited").update(created_at=timezone.now() - timedelta(hours=1))
    quick(client, "HIST-1", "status", "ebay listed")
    client.cookies[NAME_COOKIE] = "Ann"
    quick(client, "HIST-1", "status", "sold")
    statuses = [e.changes[0]["new"] for e in events() if e.action == "edited"]
    assert statuses == ["eBay Draft", "eBay Listed", "SOLD"]
    assert events()[-1].actor == "Ann"


def test_delete_and_restore_are_recorded(client):
    intake_post(client)
    item = Item.objects.get(sku_normalized="HIST-1")
    client.post(reverse("api_item_delete", args=[item.pk]), {"confirm": "DELETE"}, content_type="application/json")
    client.post(reverse("api_undo_delete"), {}, content_type="application/json")
    assert [e.action for e in events()] == ["created", "deleted", "restored"]


def test_photo_uploads_in_a_row_become_one_entry(client, image):
    intake_post(client)
    for _ in range(3):
        client.post(reverse("api_photo_upload"), {"sku": "HIST-1", "photo": image()})
    photo_events = [e for e in events() if e.action == "photo_added"]
    assert len(photo_events) == 1 and photo_events[0].note == "3 photos"


def test_photos_before_first_save_are_not_logged(client, image):
    client.post(reverse("api_photo_upload"), {"sku": "HIST-9", "photo": image()})
    assert ItemEvent.objects.count() == 0


def test_square_sale_is_credited_to_square(client, square_settings, item):
    signed_post(client, order_event())
    event = ItemEvent.objects.get(item=item)
    assert event.actor == "Square" and event.note == "Sold in Square"
    assert {"field": "status", "label": "Status", "old": "Intake", "new": "SOLD"} in event.changes


def test_history_shows_on_sheet_and_phone_card(client):
    client.cookies[NAME_COOKIE] = "Jordan"
    intake_post(client, price="5")
    intake_post(client, price="7")
    sheet = client.get(reverse("intake") + "?sku=HIST-1").content.decode()
    assert "History" in sheet and "Jordan" in sheet and "$5.00" in sheet and "$7.00" in sheet
    phone = client.get(reverse("mobile_card", args=["HIST-1"])).content.decode()
    assert "Recent changes" in phone and "changed Price" in phone


# ── Bulk changes from Lookup ─────────────────────────────────────────────────
def bulk(client, skus, field="status", value="ebay listed"):
    return client.post(reverse("api_item_bulk_update"), {"skus": skus, "field": field, "value": value},
                       content_type="application/json")


def test_bulk_status_change_updates_each_item_and_logs_it(client):
    client.cookies[NAME_COOKIE] = "Jordan"
    for sku in ("B-1", "B-2"):
        intake_post(client, sku=sku)
    response = bulk(client, ["b-1", "B-2", "B-2"]).json()
    assert response == {"ok": True, "updated": 2, "failed": []}
    assert set(Item.objects.values_list("status", flat=True)) == {"ebay listed"}
    for sku in ("B-1", "B-2"):
        assert events(sku)[-1].changes[0]["new"] == "eBay Listed"
        assert events(sku)[-1].actor == "Jordan"


def test_bulk_reports_missing_skus_but_updates_the_rest(client):
    intake_post(client, sku="B-1")
    response = bulk(client, ["B-1", "NOPE"], field="ready", value="1").json()
    assert response["updated"] == 1
    assert response["failed"] == [{"sku": "NOPE", "error": "SKU not found."}]
    assert Item.objects.get(sku_normalized="B-1").ready is True


@pytest.mark.parametrize("payload,message", [
    ({"skus": [], "field": "status", "value": "sold"}, "Select at least one item."),
    ({"skus": ["B-1"], "field": "price", "value": "1"}, "can't be changed for several items"),
    ({"skus": [f"S{i}" for i in range(201)], "field": "status", "value": "sold"}, "at most 200"),
])
def test_bulk_rejects_bad_requests(client, payload, message):
    response = client.post(reverse("api_item_bulk_update"), payload, content_type="application/json")
    assert response.status_code == 400 and message in response.json()["error"]


def test_lookup_page_has_selection_and_bulk_bar(client):
    intake_post(client, sku="B-1")
    html = client.get(reverse("lookup")).content.decode()
    assert 'data-select-all' in html and 'value="B-1"' in html and 'id="bulk-bar"' in html


def test_short_relative_times():
    from core.templatetags.ui import ago
    now = timezone.now()
    assert ago(now) == "just now"
    assert ago(now - timedelta(minutes=5)) == "5 min ago"
    assert ago(now - timedelta(hours=3, minutes=20)) == "3 hours ago"
    assert ago(None) == ""


def test_skus_only_wrap_at_spaces():
    from core.templatetags.ui import sku
    assert sku("CB2-0822-287") == '<span class="sku-part">CB2-0822-287</span>'
    assert sku("GC-6202-88 (NEED <b>)") == (
        '<span class="sku-part">GC-6202-88</span> <span class="sku-part">(NEED</span> <span class="sku-part">&lt;b&gt;)</span>'
    )
    assert sku(None) == ""


def test_expired_form_shows_friendly_page():
    from django.test import Client
    strict = Client(enforce_csrf_checks=True)
    response = strict.post(reverse("intake"), {"sku": "X-1", "what_is_it": "Laptop"})
    assert response.status_code == 403
    assert "needs a reload" in response.content.decode()
