"""Demo mode: seeding only runs in a demo copy, and risky actions are switched off there."""
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from inventory.models import Item, Photo, Status


@pytest.fixture
def demo(settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "DEMO_MODE": True}


@pytest.mark.django_db
def test_seed_demo_refuses_outside_demo_mode():
    Item.objects.create(sku="REAL-1", what_is_it="Real item")
    with pytest.raises(CommandError):
        call_command("seed_demo")
    assert Item.objects.filter(sku="REAL-1").exists()


@pytest.mark.django_db
def test_seed_demo_replaces_everything_with_made_up_items(demo):
    Item.objects.create(sku="REAL-1", what_is_it="Real item")
    call_command("seed_demo")
    assert not Item.all_objects.filter(sku="REAL-1").exists()
    assert Item.objects.count() == 48
    assert set(Item.objects.values_list("status", flat=True)) == set(Status.values)
    assert Photo.objects.count() >= 48


@pytest.mark.django_db
def test_seed_demo_is_repeatable(demo):
    call_command("seed_demo")
    first = sorted(Item.objects.values_list("sku", flat=True))
    call_command("seed_demo")
    assert sorted(Item.objects.values_list("sku", flat=True)) == first


@pytest.mark.django_db
def test_demo_turns_off_uploads_and_operator_actions(demo, client, image):
    upload = client.post(reverse("api_photo_upload"), {"sku": "DX-1", "photo": image()})
    assert upload.status_code == 403
    assert client.post(reverse("api_backup")).status_code == 403


@pytest.mark.django_db
def test_demo_banner_shows_only_in_demo(client, settings):
    assert b"demo-banner" not in client.get(reverse("dashboard")).content
    settings.PINKSHEET = {**settings.PINKSHEET, "DEMO_MODE": True}
    assert b"demo-banner" in client.get(reverse("dashboard")).content
