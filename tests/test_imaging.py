"""Imaging: the imaging app sends JSON about each computer; specs fill in the linked item."""
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from imaging import ingest
from imaging.models import DeviceReport
from inventory.models import Item, ItemEvent

pytestmark = pytest.mark.django_db
API = "/api/imaging/report/"
KEY = "test-imaging-key"

SAMPLE = {
    "serial": "TEST5500A", "manufacturer": "Dell Inc.", "model": "Latitude 5500",
    "cpu": "Intel Core i5-8365U", "ram_gb": 8,
    "disk_model": "SAMSUNG MZVLB256HBHQ", "disk_serial": "S4DXNX0M123456", "disk_gb": 256, "disk_bus": "NVMe",
    "mac_address": "AA:BB:CC:DD:EE:FF", "ip_address": "192.168.1.50",
    "firmware": "UEFI", "secure_boot": "Enabled", "tpm": "2.0",
    "windows_edition": "Windows 11 Pro",
    "battery_present": True, "battery_health_pct": None, "battery_cycles": None,
    "status": "Imaging", "stage": "Hardware detected", "progress": 5, "message": "PXE deployment started",
}


@pytest.fixture
def api_key(settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "IMAGING_API_KEY": KEY}


def send(client, data, key=KEY, **extra):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {key}"} if key else {}
    return client.post(API, json.dumps(data), content_type="application/json", **headers, **extra)


def test_specs_are_written_the_way_items_are():
    assert ingest.item_specs(SAMPLE) == {
        "serial_number": "TEST5500A", "brand_model": "Dell Latitude 5500", "cpu": "i5-8365U",
        "ram": "8GB", "ssd_gb": "256GB", "os": "Windows 11 Pro",
    }
    assert ingest.short_cpu("Intel(R) Core(TM) i7-10610U CPU @ 1.80GHz") == "i7-10610U"
    assert ingest.short_cpu("AMD Ryzen 5 PRO 5650U with Radeon Graphics") == "AMD Ryzen 5 PRO 5650U"
    assert ingest.brand_model("LENOVO", "ThinkPad T480") == "Lenovo ThinkPad T480"
    assert ingest.brand_model("HP", "HP EliteBook 840 G6") == "HP EliteBook 840 G6"
    assert ingest.item_specs({"serial": "X", "battery_health_pct": 87.4})["battery_health"] == "87%"


def test_report_is_saved_and_updated_by_serial(client, api_key):
    response = send(client, SAMPLE)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "received": 1, "computers": [{"serial": "TEST5500A", "item": None}]}
    send(client, {"serial": "test5500a", "status": "Imaging", "stage": "Applying image", "progress": 60, "message": ""})
    report = DeviceReport.objects.get()
    assert report.progress == 60 and report.stage == "Applying image"
    assert report.payload["cpu"] == "Intel Core i5-8365U"  # hardware from the first report is kept
    assert [entry["progress"] for entry in report.log] == [60, 5]


def test_api_needs_the_key(client, api_key):
    assert send(client, SAMPLE, key="").status_code == 401
    assert send(client, SAMPLE, key="wrong").status_code == 401
    ok = client.post(API, json.dumps(SAMPLE), content_type="application/json", HTTP_X_API_KEY=KEY)
    assert ok.status_code == 200


def test_api_is_off_until_a_key_is_set(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "IMAGING_API_KEY": ""}
    assert send(client, SAMPLE).status_code == 503


def test_api_works_with_sign_in_required(client, api_key, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "REQUIRE_LOGIN": True}
    assert send(client, SAMPLE).status_code == 200
    assert client.get("/imaging/").status_code == 302  # the page itself still needs sign-in


def test_api_only_from_the_local_network(client, api_key):
    assert send(client, SAMPLE, REMOTE_ADDR="8.8.8.8").status_code == 403


def test_bad_json_is_rejected(client, api_key):
    bad = client.post(API, "{not json", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {KEY}")
    assert bad.status_code == 400 and "valid JSON" in bad.json()["error"]
    no_serial = send(client, {"model": "Latitude"})
    assert no_serial.status_code == 400 and "serial" in no_serial.json()["error"]
    assert not DeviceReport.objects.exists()


def test_a_list_of_computers_is_accepted(client, api_key):
    response = send(client, [SAMPLE, {**SAMPLE, "serial": "OTHER1"}])
    assert response.json()["received"] == 2 and DeviceReport.objects.count() == 2


def test_matching_serial_fills_only_blank_fields(client, api_key):
    item = Item.objects.create(sku="LAP-1", what_is_it="Laptop", serial_number="test5500a", ram="16gb")
    response = send(client, SAMPLE)
    assert response.json()["computers"][0]["item"] == "LAP-1"
    item.refresh_from_db()
    assert item.ram == "16gb"  # what staff typed is kept
    assert item.cpu == "i5-8365U" and item.ssd_gb == "256GB" and item.os == "Windows 11 Pro"
    assert item.brand_model == "Dell Latitude 5500"
    event = ItemEvent.objects.filter(item=item, action=ItemEvent.Action.EDITED).get()
    assert event.actor == "Imaging"
    # Progress-only updates don't add more history.
    send(client, {"serial": "TEST5500A", "progress": 90})
    assert ItemEvent.objects.filter(item=item).count() == 1


def test_linking_by_sku_fills_the_item_and_names_the_person(client, api_key, django_user_model):
    send(client, SAMPLE)
    report = DeviceReport.objects.get()
    item = Item.objects.create(sku="LAP-2", what_is_it="Laptop")
    client.force_login(django_user_model.objects.create_user("sam", password="x", first_name="Sam"))
    page = client.post(f"/imaging/{report.pk}/link/", {"sku": "lap-2"}, follow=True)
    assert "Linked TEST5500A to LAP-2" in page.content.decode()
    item.refresh_from_db()
    assert item.serial_number == "TEST5500A" and item.cpu == "i5-8365U"
    assert ItemEvent.objects.get(item=item, action=ItemEvent.Action.EDITED).actor == "Sam"
    report.refresh_from_db()
    assert report.item == item


def test_cannot_link_to_an_item_with_a_different_serial(client, api_key):
    send(client, SAMPLE)
    report = DeviceReport.objects.get()
    Item.objects.create(sku="LAP-3", what_is_it="Laptop", serial_number="ZZZ999")
    page = client.post(f"/imaging/{report.pk}/link/", {"sku": "LAP-3"}, follow=True)
    assert "already has a different serial number" in page.content.decode()
    report.refresh_from_db()
    assert report.item is None


def test_unknown_sku_is_reported(client, api_key):
    send(client, SAMPLE)
    report = DeviceReport.objects.get()
    page = client.post(f"/imaging/{report.pk}/link/", {"sku": "NOPE"}, follow=True)
    assert "No item with SKU NOPE" in page.content.decode()


def test_serial_typed_on_an_item_later_links_it_on_the_page(client, api_key):
    send(client, SAMPLE)
    item = Item.objects.create(sku="LAP-4", what_is_it="Laptop", serial_number="TEST5500A")
    html = client.get("/imaging/").content.decode()
    assert "Linked to" in html and "LAP-4" in html
    assert DeviceReport.objects.get().item == item


def test_page_shows_progress_specs_and_details(client, api_key):
    send(client, SAMPLE)
    html = client.get("/imaging/").content.decode()
    assert "Dell Latitude 5500" in html and "TEST5500A" in html
    assert "Hardware detected" in html and "PXE deployment started" in html and 'aria-valuenow="5"' in html
    assert "i5-8365U" in html and "256GB NVMe" in html and "AA:BB:CC:DD:EE:FF" in html and "MAC address" in html and "Disk serial" in html
    assert "/intake/?new=1&device=" in html
    assert 'href="/imaging/"' in html  # in the sidebar


def test_start_intake_prefills_the_sheet(client, api_key):
    send(client, SAMPLE)
    report = DeviceReport.objects.get()
    html = client.get(f"/intake/?new=1&device={report.pk}").content.decode()
    assert 'value="i5-8365U"' in html and 'value="Dell Latitude 5500"' in html and 'value="256GB"' in html
    assert f'name="device" value="{report.pk}"' in html
    # Saving the sheet links the computer, which also puts its serial on the new item.
    client.post("/intake/", {
        "sku": "LAP-9", "what_is_it": "Laptop", "status": "intake", "quantity": "1",
        "cpu": "i5-8365U", "ram": "8GB", "device": str(report.pk),
    })
    item = Item.objects.get(sku_normalized="LAP-9")
    assert item.serial_number == "TEST5500A" and item.ssd_gb == "256GB" and item.ram == "8GB"
    report.refresh_from_db()
    assert report.item == item
    # Later reports from the imaging app now land on this item.
    assert send(client, {"serial": "TEST5500A", "progress": 100}).json()["computers"][0]["item"] == "LAP-9"


def test_bad_device_id_on_intake_is_ignored(client):
    assert client.get("/intake/?new=1&device=999").status_code == 200
    assert client.get("/intake/?new=1&device=abc").status_code == 200


def test_upload_json_files(client):
    files = [
        SimpleUploadedFile("a.json", json.dumps(SAMPLE).encode(), content_type="application/json"),
        SimpleUploadedFile("b.json", b"not json", content_type="application/json"),
    ]
    page = client.post("/imaging/upload/", {"files": files}, follow=True).content.decode()
    assert "Added 1 computer" in page and "b.json: That isn" in page
    assert DeviceReport.objects.get().reported_by.startswith("Device ")


def test_setup_hint_when_no_key(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "IMAGING_API_KEY": ""}
    assert "PINKSHEET_IMAGING_API_KEY" in client.get("/imaging/").content.decode()


def test_remove_and_unlink(client, api_key):
    item = Item.objects.create(sku="LAP-5", what_is_it="Laptop", serial_number="TEST5500A")
    send(client, SAMPLE)
    report = DeviceReport.objects.get()
    client.post(f"/imaging/{report.pk}/unlink/")
    report.refresh_from_db()
    assert report.item is None
    client.post(f"/imaging/{report.pk}/remove/")
    assert not DeviceReport.objects.exists() and Item.objects.filter(pk=item.pk).exists()


def test_refresh_returns_just_the_list(client, api_key):
    send(client, SAMPLE)
    html = client.get("/imaging/", HTTP_X_REFRESH="1").content.decode()
    assert "imaging-card" in html and "<html" not in html
