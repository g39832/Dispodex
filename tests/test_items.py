from decimal import Decimal

import pytest
from django.urls import reverse

from inventory.models import IntakeDraft, Item, Photo, Review, Status, coerce_status
from inventory.services import items as item_service
from squaresync.models import SyncJob

pytestmark = pytest.mark.django_db(transaction=True)


def intake_post(client, **fields):
    data = {"sku": "NEW-1", "what_is_it": "Laptop", "status": "intake", "quantity": "1", **fields}
    return client.post(reverse("intake"), data)


def test_sku_is_normalized_on_save():
    item = Item.objects.create(sku="  ab-12 ", what_is_it="x")
    assert item.sku == "AB-12"
    assert item.sku_normalized == "AB-12"


@pytest.mark.parametrize(
    "raw,expected",
    [("SOLD", "sold"), ("Tested", "ebay draft"), ("eBay Listed", "ebay listed"), ("weird", "intake"), ("", "intake")],
)
def test_legacy_statuses_map_to_lanes(raw, expected):
    assert coerce_status(raw) == expected


def test_intake_creates_item_and_queues_square(client):
    response = intake_post(client, price="$1,249.50", functional="Yes", condition="Fair", notes="Cracked hinge")
    assert response.status_code == 302
    item = Item.objects.get(sku_normalized="NEW-1")
    assert item.price == Decimal("1249.50")
    assert item.functional == "Yes"
    assert item.condition == "Fair"
    assert SyncJob.objects.filter(sku_normalized="NEW-1", operation="catalog_upsert").exists()


def test_saving_existing_sku_updates_instead_of_duplicating(client):
    intake_post(client, sku="dup-1", what_is_it="Laptop")
    intake_post(client, sku="DUP-1", what_is_it="Desktop")
    assert Item.objects.filter(sku_normalized="DUP-1").count() == 1
    assert Item.objects.get(sku_normalized="DUP-1").what_is_it == "Desktop"


def test_intake_requires_sku_and_what_is_it(client):
    response = client.post(reverse("intake"), {"sku": "", "what_is_it": ""})
    assert response.status_code == 400
    assert Item.objects.count() == 0
    assert b"Please fill in the SKU" in response.content


def test_invalid_price_is_rejected(client):
    response = intake_post(client, price="abc")
    assert response.status_code == 400
    assert not Item.objects.exists()


def test_removed_square_checkboxes_keep_old_answers(client):
    """The Square / "do we care" boxes are gone from the sheet; saving must not wipe older answers."""
    intake_post(client, sku="OLD-SQ")
    Item.objects.filter(sku_normalized="OLD-SQ").update(is_square=True, care_if_square=True)
    intake_post(client, sku="OLD-SQ", is_square="", notes="edited later")
    item = Item.objects.get(sku_normalized="OLD-SQ")
    assert item.is_square and item.care_if_square and item.notes == "edited later"
    html = client.get(reverse("intake") + "?sku=OLD-SQ").content.decode()
    assert 'name="is_square"' not in html and 'name="care_if_square"' not in html


@pytest.mark.parametrize("grade", ["Scrap", "Poor", "Fair", "Good", "Great", "Excellent"])
def test_every_condition_grade_saves(client, grade):
    intake_post(client, condition=grade)
    assert Item.objects.get().condition == grade


def test_old_unicorn_grade_is_rejected_on_the_sheet(client):
    response = intake_post(client, condition="Unicorn")
    assert response.status_code == 400
    assert not Item.objects.exists()


def test_condition_chips_are_ordered_worst_to_best(client):
    html = client.get(reverse("intake") + "?new=1").content.decode()
    order = [html.index(f'value="{g}"') for g in ("Scrap", "Poor", "Fair", "Good", "Great", "Excellent")]
    assert order == sorted(order) and "Unicorn" not in html


def test_save_clears_the_autosave_draft(client):
    IntakeDraft.objects.create(sku_normalized="NEW-1", payload={"what_is_it": "draft"})
    intake_post(client)
    assert not IntakeDraft.objects.exists()


def test_renaming_sku_moves_its_photos(client, image):
    intake_post(client, sku="OLD-1")
    client.post(reverse("api_photo_upload"), {"sku": "OLD-1", "photo": image()})
    item = Item.objects.get()
    intake_post(client, sku="RENAMED-1", id=str(item.pk))
    photo = Photo.objects.get()
    assert photo.sku_normalized == "RENAMED-1"
    from inventory.services.photos import photo_path

    assert photo_path(photo).exists()


def test_renaming_onto_another_items_sku_is_blocked(client):
    intake_post(client, sku="A-1")
    intake_post(client, sku="B-1")
    b = Item.objects.get(sku_normalized="B-1")
    response = intake_post(client, sku="A-1", id=str(b.pk))
    assert response.status_code == 400
    assert b"already belongs to another item" in response.content


def test_save_and_new_redirects_to_blank_sheet(client):
    response = intake_post(client, save_and_new="on")
    assert response["Location"].startswith("/intake/?new=1")


def test_quick_update_status_to_sold_sets_badge(client, item):
    response = client.post(reverse("api_item_update", args=[item.sku]), {"field": "status", "value": "sold"},
                           content_type="application/json")
    assert response.json()["ok"] is True
    item.refresh_from_db()
    assert item.status == Status.SOLD
    assert item.reviewed == Review.SOLD


def test_leaving_sold_clears_sold_badge(item):
    item_service.quick_update(item.sku, "status", "sold")
    item_service.quick_update(item.sku, "status", "ebay listed")
    item.refresh_from_db()
    assert item.reviewed == Review.INACTIVE


@pytest.mark.parametrize("field,value,attr,expected", [
    ("price", "19.99", "price", Decimal("19.99")),
    ("price", "", "price", None),
    ("quantity", "0", "quantity", 1),
    ("quantity", "7", "quantity", 7),
    ("ready", "1", "ready", True),
    ("reviewed", "1", "reviewed", 1),
    ("notes", "hello", "notes", "hello"),
])
def test_quick_update_fields(item, field, value, attr, expected):
    item_service.quick_update(item.sku, field, value)
    item.refresh_from_db()
    assert getattr(item, attr) == expected


def test_quick_update_rejects_unknown_field(client, item):
    response = client.post(reverse("api_item_update", args=[item.sku]), {"field": "sku", "value": "X"},
                           content_type="application/json")
    assert response.status_code == 400


def test_quick_update_missing_sku_is_404(client):
    response = client.post(reverse("api_item_update", args=["NOPE"]), {"field": "status", "value": "sold"},
                           content_type="application/json")
    assert response.status_code == 404


def test_delete_requires_confirmation_word(client, item):
    response = client.post(reverse("api_item_delete", args=[item.pk]), {"confirm": "yes"}, content_type="application/json")
    assert response.status_code == 400
    assert Item.objects.filter(pk=item.pk).exists()


def test_delete_and_undo(client, item):
    response = client.post(reverse("api_item_delete", args=[item.pk]), {"confirm": "DELETE"}, content_type="application/json")
    assert response.json()["ok"]
    assert not Item.objects.filter(pk=item.pk).exists()
    assert Item.all_objects.filter(pk=item.pk, deleted_at__isnull=False).exists()
    response = client.post(reverse("api_undo_delete"))
    assert response.json()["item"]["sku"] == "ABC-1"
    assert Item.objects.filter(pk=item.pk).exists()


def test_undo_refuses_when_sku_was_recreated(item):
    item_service.soft_delete(item.pk)
    Item.objects.create(sku="ABC-1", what_is_it="Replacement")
    with pytest.raises(item_service.ItemError):
        item_service.undo_last_delete()


def test_deleted_sku_can_be_reused(item):
    item_service.soft_delete(item.pk)
    Item.objects.create(sku="ABC-1", what_is_it="New one")  # must not violate the unique rule
    assert Item.objects.filter(sku_normalized="ABC-1").count() == 1


def test_copy_fields_excludes_sku(client, item):
    data = client.get(reverse("api_item_copy", args=["abc-1"])).json()["data"]
    assert "sku" not in data and "id" not in data
    assert data["brand_model"] == "Dell 5590"
    assert data["price"] == "120.00"


def test_intake_page_loads_existing_item(client, item):
    response = client.get(reverse("intake") + "?sku=abc-1")
    assert response.status_code == 200
    assert b'value="ABC-1"' in response.content
    assert b"Dell 5590" in response.content
