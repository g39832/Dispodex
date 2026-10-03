"""Security: sign-in, CSRF, network limits, injection, uploads, webhooks and file serving."""
import base64
import csv
import hashlib
import hmac
import io
import json
import zipfile

import openpyxl
import pytest
from django.test import Client
from django.urls import reverse
from PIL import Image

from core.actor import NAME_COOKIE, clean_name
from core.network import is_private_request
from core.skus import escape_like, sanitize_filename, sku_directory
from inventory.models import Item, Photo
from inventory.services.search import ItemFilters, filter_items
from squaresync.models import WebhookEvent
from teamdocs.models import DocPost
from tests.conftest import make_image

pytestmark = pytest.mark.django_db

XSS = '<script>alert("x")</script>'
FORMULAS = ["=HYPERLINK(\"http://evil.example\",\"x\")", "+1+1", "-1+1", "@SUM(A1)", "\t=1", "\r=1"]


def login_on(settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "REQUIRE_LOGIN": True}


def csrf_client(**extra):
    return Client(enforce_csrf_checks=True, REMOTE_ADDR="127.0.0.1", **extra)


class Req:
    """Just enough of a request for is_private_request()."""

    def __init__(self, addr, **meta):
        self.META = {"REMOTE_ADDR": addr, **meta}


# ── sign-in wall ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", [
    "/", "/intake/", "/lookup/", "/board/", "/archive/", "/scripts/", "/system/", "/imaging/", "/docs/",
    "/docs/new/", "/report-bug/", "/m/ABC-1/", "/print/ABC-1/", "/card/ABC-1/", "/scripts/listing-notes/",
    "/exports/inventory.csv", "/exports/partner.csv", "/exports/archive.csv", "/exports/inventory.xlsx",
    "/photos/1/", "/docs/photos/1/", "/listing-images/files/ABC/x.png", "/qz/certificate/", "/lookup.php",
])
def test_every_page_and_file_needs_sign_in(client, settings, url):
    login_on(settings)
    response = client.get(url)
    assert response.status_code == 302 and response["Location"].startswith("/login/?next=")


@pytest.mark.parametrize("url", [
    "/api/items/ABC-1/", "/api/drafts/ABC-1/", "/api/suggestions/?q=a", "/api/board/cards/", "/api/photos/?sku=A",
    "/api/scripts/ABC-1/", "/api/labels/zpl/?sku=A", "/api/ebay-categories/", "/api/square/status/",
    "/api/square/sync-all/progress/", "/api/reconciliation/",
])
def test_every_api_read_needs_sign_in(client, settings, url):
    login_on(settings)
    response = client.get(url)
    assert response.status_code == 401 and response.json()["ok"] is False


@pytest.mark.parametrize("url", [
    "/api/items/ABC-1/update/", "/api/items/bulk-update/", "/api/items/1/delete/", "/api/items/undo-delete/",
    "/api/photos/upload/", "/api/photos/1/delete/", "/api/photos/reorder/", "/api/script-build/",
    "/api/ops/backup/", "/api/ops/import-database/", "/api/square/sync-all/", "/api/square/retry/",
    "/api/reconciliation/run/",
])
def test_every_api_change_needs_sign_in(client, settings, url):
    login_on(settings)
    item = Item.objects.create(sku="ABC-1", what_is_it="Laptop", notes="keep")
    response = client.post(url, {"field": "notes", "value": "hacked", "confirm": "DELETE"})
    assert response.status_code == 401
    item.refresh_from_db()
    assert item.notes == "keep" and item.deleted_at is None


def test_signed_out_people_cannot_save_the_intake_sheet(client, settings):
    login_on(settings)
    client.post("/intake/", {"sku": "NEW-1", "what_is_it": "Laptop", "status": "intake", "quantity": "1"})
    assert not Item.objects.exists()


def test_look_alike_paths_are_not_let_through_the_wall(client, settings):
    login_on(settings)
    # Only the exact open addresses skip sign-in, not anything that merely starts with a similar word.
    for url in ("/api/healthcheck/", "/webhooks/../exports/inventory.csv", "/static/../system/"):
        assert client.get(url).status_code in (302, 401, 404)
        assert b"SKU" not in client.get(url).content


def test_wall_stays_off_for_square_webhooks_and_health(client, settings):
    login_on(settings)
    assert client.get("/api/health/").status_code == 200
    assert client.post("/webhooks/square/", b"{}", content_type="application/json").status_code != 302


def test_sign_in_does_not_redirect_to_other_sites(client, settings, django_user_model):
    login_on(settings)
    django_user_model.objects.create_user("sam", password="pw12345678")
    for target in ("https://evil.example/", "//evil.example/", "/\\evil.example", "http:/evil.example"):
        response = client.post(f"/login/?next={target}", {"username": "sam", "password": "pw12345678"})
        assert response.status_code == 302
        assert "evil.example" not in response["Location"]
        client.post("/logout/")


def test_wrong_password_is_refused_without_saying_which_part_was_wrong(client, settings, django_user_model):
    login_on(settings)
    django_user_model.objects.create_user("sam", password="pw12345678")
    wrong_password = client.post("/login/", {"username": "sam", "password": "nope"})
    no_such_user = client.post("/login/", {"username": "ghost", "password": "nope"})
    assert wrong_password.status_code == no_such_user.status_code == 200
    assert "_auth_user_id" not in client.session
    # The same message for both, so the page can't be used to find real usernames.
    assert wrong_password.context["form"].errors == no_such_user.context["form"].errors


def test_turned_off_accounts_cannot_sign_in(client, settings, django_user_model):
    login_on(settings)
    django_user_model.objects.create_user("gone", password="pw12345678", is_active=False)
    client.post("/login/", {"username": "gone", "password": "pw12345678"})
    assert client.get("/").status_code == 302


def test_signing_in_gets_a_new_session_id(client, settings, django_user_model):
    login_on(settings)
    django_user_model.objects.create_user("sam", password="pw12345678")
    client.get("/login/")
    client.session.save()
    before = client.cookies["sessionid"].value if "sessionid" in client.cookies else None
    client.post("/login/", {"username": "sam", "password": "pw12345678"})
    assert client.cookies["sessionid"].value != before


def test_sign_out_needs_a_post(client, settings, django_user_model):
    login_on(settings)
    client.force_login(django_user_model.objects.create_user("sam", password="x"))
    assert client.get("/logout/").status_code == 405  # a link or <img> on another page can't sign people out
    assert client.get("/").status_code == 200


def test_session_and_csrf_cookies_are_not_readable_by_scripts_on_other_sites(client, settings, django_user_model):
    login_on(settings)
    django_user_model.objects.create_user("sam", password="pw12345678")
    client.post("/login/", {"username": "sam", "password": "pw12345678"})
    cookie = client.cookies["sessionid"]
    assert cookie["httponly"] and cookie["samesite"] == "Lax"


def test_passwords_are_never_stored_in_plain_text(django_user_model):
    user = django_user_model.objects.create_user("sam", password="pw12345678")
    assert "pw12345678" not in user.password


def test_weak_passwords_are_refused():
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError

    for weak in ("short", "password123", "12345678"):
        with pytest.raises(ValidationError):
            validate_password(weak)


def test_regular_staff_cannot_open_the_admin(client, django_user_model):
    client.force_login(django_user_model.objects.create_user("sam", password="x"))
    assert client.get("/admin/auth/user/").status_code == 302


# ── CSRF: other websites can't make a signed-in browser change things ────────
@pytest.mark.parametrize("url,data", [
    ("/intake/", {"sku": "CSRF-1", "what_is_it": "Laptop", "status": "intake", "quantity": "1"}),
    ("/api/items/ABC-1/update/", {"field": "notes", "value": "hacked"}),
    ("/api/items/bulk-update/", {"skus": ["ABC-1"], "field": "status", "value": "sold"}),
    ("/api/items/1/delete/", {"confirm": "DELETE"}),
    ("/api/ops/backup/", {}),
    ("/docs/new/", {"name": "x", "title": "x"}),
    ("/report-bug/", {"name": "x", "summary": "x"}),
    ("/scripts/listing-notes/", {"text": "hacked"}),
    ("/imaging/1/remove/", {}),
])
def test_changes_need_the_csrf_token(url, data):
    Item.objects.create(sku="ABC-1", what_is_it="Laptop", notes="keep")
    response = csrf_client().post(url, json.dumps(data), content_type="application/json")
    assert response.status_code == 403
    assert Item.objects.get(sku_normalized="ABC-1").notes == "keep"
    assert not Item.objects.filter(sku_normalized="CSRF-1").exists()
    assert not DocPost.objects.exists()


def test_csrf_token_from_the_page_lets_the_change_through():
    Item.objects.create(sku="ABC-1", what_is_it="Laptop")
    browser = csrf_client()
    browser.get("/")
    token = browser.cookies["csrftoken"].value
    response = browser.post("/api/items/ABC-1/update/", json.dumps({"field": "notes", "value": "ok"}),
                            content_type="application/json", HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 200 and Item.objects.get().notes == "ok"


def test_csrf_check_rejects_another_sites_origin_over_https(settings):
    settings.CSRF_TRUSTED_ORIGINS = []
    browser = csrf_client()
    browser.get("/")
    token = browser.cookies["csrftoken"].value
    response = browser.post("/api/items/undo-delete/", HTTP_X_CSRFTOKEN=token, secure=True,
                            HTTP_ORIGIN="https://evil.example", HTTP_REFERER="https://evil.example/")
    assert response.status_code == 403


# ── local-network-only actions ───────────────────────────────────────────────
@pytest.mark.parametrize("addr,private", [
    ("127.0.0.1", True), ("10.0.0.5", True), ("192.168.1.20", True), ("172.16.4.1", True),
    ("100.64.0.1", True), ("100.127.255.254", True), ("::1", True), ("::ffff:192.168.1.5", True),
    ("8.8.8.8", False), ("100.128.0.1", False), ("::ffff:8.8.8.8", False), ("2001:4860:4860::8888", False),
    ("", False), ("not-an-ip", False), ("127.0.0.1, 8.8.8.8", False),
])
def test_which_addresses_count_as_the_shop_network(addr, private):
    assert is_private_request(Req(addr)) is private


def test_forwarded_for_header_cannot_fake_a_local_address():
    assert not is_private_request(Req("8.8.8.8", HTTP_X_FORWARDED_FOR="127.0.0.1", HTTP_X_REAL_IP="127.0.0.1"))


@pytest.mark.parametrize("url", [
    "/api/ops/backup/", "/api/ops/verify/", "/api/ops/import-database/", "/api/square/sync-all/",
    "/api/square/queue-all/", "/api/square/test/", "/api/square/retry/", "/api/reconciliation/run/",
    "/api/photos/1/delete/", "/api/photos/1/thumbnail/", "/api/photos/reorder/", "/api/imaging/report/",
])
def test_operator_actions_refuse_the_internet(url):
    outsider = Client(REMOTE_ADDR="8.8.8.8", HTTP_X_FORWARDED_FOR="127.0.0.1")
    response = outsider.post(url, {"ids": [1, 2]})
    assert response.status_code == 403 and response.json()["ok"] is False


def test_listing_notes_cannot_be_changed_from_the_internet(settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "EBAY_BOILERPLATE_FILE": settings.DATA_DIR / "notes-sec.txt"}
    Client(REMOTE_ADDR="8.8.8.8").post("/scripts/listing-notes/", {"text": "hacked"})
    assert not (settings.DATA_DIR / "notes-sec.txt").exists()


# ── access control between people ────────────────────────────────────────────
def test_quick_edit_cannot_touch_protected_fields(client):
    Item.objects.create(sku="ABC-1", what_is_it="Laptop")
    for field in ("sku", "sku_normalized", "deleted_at", "id", "created_at", "what_is_it", "__class__", ""):
        response = client.post("/api/items/ABC-1/update/", {"field": field, "value": "X"}, content_type="application/json")
        assert response.status_code == 400
    item = Item.objects.get()
    assert item.sku == "ABC-1" and item.deleted_at is None and item.what_is_it == "Laptop"


def test_bulk_edit_only_allows_safe_fields_and_a_limited_count(client):
    Item.objects.create(sku="ABC-1", what_is_it="Laptop", price="10")
    assert client.post("/api/items/bulk-update/", {"skus": ["ABC-1"], "field": "price", "value": "0"},
                       content_type="application/json").status_code == 400
    many = [f"S-{n}" for n in range(201)]
    assert client.post("/api/items/bulk-update/", {"skus": many, "field": "status", "value": "sold"},
                       content_type="application/json").status_code == 400
    assert str(Item.objects.get().price) == "10.00"


def test_bad_quick_edit_values_are_refused(client):
    Item.objects.create(sku="ABC-1", what_is_it="Laptop", price="10")
    for field, value in (("price", "-5"), ("price", "abc"), ("price", "1e999999"), ("quantity", "lots")):
        response = client.post("/api/items/ABC-1/update/", {"field": field, "value": value}, content_type="application/json")
        assert response.status_code == 400, (field, value)
    assert str(Item.objects.get().price) == "10.00"


def test_signed_in_worker_cannot_edit_pin_or_delete_someone_elses_post(client, django_user_model):
    author = django_user_model.objects.create_user("ann", password="x")
    post = DocPost.objects.create(title="Rules", body="Original", author="Ann", author_user=author)
    client.force_login(django_user_model.objects.create_user("bob", password="x"))
    client.post(f"/docs/{post.pk}/edit/", {"title": "Hacked", "body": "Hacked"})
    client.post(f"/docs/{post.pk}/pin/")
    client.post(f"/docs/{post.pk}/delete/")
    post.refresh_from_db()
    assert post.title == "Rules" and post.body == "Original" and post.pinned_at is None


def test_doc_edit_cannot_remove_photos_from_another_post(client, image):
    first = DocPost.objects.create(title="One", author="Ann")
    second = DocPost.objects.create(title="Two", author="Ann")
    client.cookies[NAME_COOKIE] = "Ann"
    client.post(f"/docs/{first.pk}/edit/", {"title": "One", "photos": [image()]})
    photo = first.photos.get()
    client.post(f"/docs/{second.pk}/edit/", {"title": "Two", "remove_photo": [str(photo.pk)]})
    assert first.photos.filter(pk=photo.pk).exists()


def test_signed_in_non_staff_cannot_change_listing_notes(client, settings, django_user_model):
    settings.PINKSHEET = {**settings.PINKSHEET, "EBAY_BOILERPLATE_FILE": settings.DATA_DIR / "notes-sec2.txt"}
    client.force_login(django_user_model.objects.create_user("sam", password="x"))
    client.post("/scripts/listing-notes/", {"text": "hacked"})
    assert not (settings.DATA_DIR / "notes-sec2.txt").exists()


def test_staff_cannot_promote_themselves_to_superuser(client, django_user_model):
    lead = django_user_model.objects.create_user("lead", password="x", is_staff=True)
    client.force_login(lead)
    page = client.get(f"/admin/auth/user/{lead.pk}/change/")
    form = page.context["adminform"].form
    data = {k: v for k, v in form.initial.items() if v is not None and not isinstance(v, (list, tuple))}
    data.update({"username": "lead", "is_superuser": "on", "is_staff": "on", "is_active": "on",
                 "date_joined_0": "2026-01-01", "date_joined_1": "00:00:00"})
    client.post(f"/admin/auth/user/{lead.pk}/change/", data)
    lead.refresh_from_db()
    assert not lead.is_superuser


# ── XSS: what people type is shown as text, never run ───────────────────────
@pytest.fixture
def nasty_item(db):
    return Item.objects.create(sku="XSS-1", what_is_it=XSS, brand_model=XSS, notes=XSS, cpu=XSS, serial_number=XSS)


@pytest.mark.parametrize("url", [
    "/intake/?sku=XSS-1", "/lookup/?q=XSS", "/m/XSS-1/", "/print/XSS-1/", "/", "/scripts/?sku=XSS-1",
])
def test_item_text_is_escaped_on_every_page(client, nasty_item, url):
    html = client.get(url).content.decode()
    assert XSS not in html


def test_typed_search_and_skus_are_escaped(client):
    for url in (f"/lookup/?q={XSS}", f"/board/?highlight={XSS}", f"/imaging/?q={XSS}", f"/docs/?q={XSS}",
                f"/archive/?q={XSS}", f"/report-bug/?from={XSS}", f"/m/{XSS}/"):
        html = client.get(url).content.decode()
        assert XSS not in html and "<script>alert" not in html, url


def test_board_highlight_cannot_break_out_of_its_json(client):
    html = client.get("/board/?highlight=</script><script>alert(1)</script>").content.decode()
    assert "</script><script>alert(1)" not in html and "<SCRIPT>ALERT(1)" not in html


def test_doc_posts_are_escaped_and_only_web_links_are_clickable(client):
    client.cookies[NAME_COOKIE] = "Ann"
    client.post("/docs/new/", {"title": XSS, "category": XSS,
                               "body": f"{XSS}\njavascript:alert(1)\n<a href=\"javascript:alert(1)\">x</a>"})
    post = DocPost.objects.get()
    html = client.get(f"/docs/{post.pk}/").content.decode() + client.get("/docs/").content.decode()
    assert XSS not in html
    assert 'href="javascript:' not in html.lower()


def test_names_from_the_cookie_cannot_inject_html(client):
    client.cookies[NAME_COOKIE] = '"><img src=x onerror=alert(1)>'
    html = client.get("/").content.decode()
    assert "<img src=x onerror" not in html
    assert "<" not in clean_name("<b>") and "\n" not in clean_name("a\r\nSet-Cookie: x=1")


def test_imaging_report_values_are_escaped(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "IMAGING_API_KEY": "secret"}
    client.post("/api/imaging/report/", {"serial": "SN-X", "manufacturer": XSS, "model": XSS, XSS: XSS},
                content_type="application/json", HTTP_AUTHORIZATION="Bearer secret")
    html = client.get("/imaging/").content.decode()
    assert "SN-X" in html and XSS not in html


def test_api_answers_are_json_not_html(client, nasty_item):
    response = client.get("/api/items/XSS-1/")
    assert response["Content-Type"] == "application/json"
    assert response["X-Content-Type-Options"] == "nosniff"


# ── spreadsheet formula injection in exports ─────────────────────────────────
def _formula_items():
    for n, text in enumerate(FORMULAS):
        Item.objects.create(sku=f"F-{n}", what_is_it=text, brand_model=text, notes=text, cpu=text)


def _csv(response):
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))


def _starts_like_formula(value) -> bool:
    return isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r")


@pytest.mark.parametrize("url", ["/exports/inventory.csv", "/exports/partner.csv"])
def test_csv_exports_neutralise_formulas(client, url):
    _formula_items()
    rows = _csv(client.get(url))
    cells = [cell for row in rows[1:] for cell in row]
    assert not [cell for cell in cells if _starts_like_formula(cell)]
    assert any("HYPERLINK" in cell for cell in cells)  # the text itself is kept


@pytest.mark.parametrize("url", ["/exports/inventory.xlsx", "/exports/partner.xlsx", "/exports/partner-sortable.xlsx"])
def test_excel_exports_never_contain_live_formulas(client, url):
    _formula_items()
    book = openpyxl.load_workbook(io.BytesIO(b"".join(client.get(url).streaming_content)))
    cells = [cell for row in book.active.iter_rows(min_row=2) for cell in row]
    assert not [cell.value for cell in cells if cell.data_type == "f"]
    assert not [cell.value for cell in cells if _starts_like_formula(cell.value)]


def test_zip_export_csv_neutralises_formulas(client):
    _formula_items()
    archive = zipfile.ZipFile(io.BytesIO(b"".join(client.get("/exports/inventory.zip").streaming_content)))
    name = next(n for n in archive.namelist() if n.endswith(".csv"))
    rows = list(csv.reader(io.StringIO(archive.read(name).decode("utf-8-sig"))))
    assert not [cell for row in rows[1:] for cell in row if _starts_like_formula(cell)]


def test_archive_csv_neutralises_formulas(client):
    from archive.models import ArchiveItem

    ArchiveItem.objects.create(sku="A-1", sku_normalized="A-1", title="=cmd|'/c calc'!A1", notes="+1", buyer="@x")
    rows = _csv(client.get("/exports/archive.csv"))
    assert not [cell for row in rows[1:] for cell in row if _starts_like_formula(cell)]


def test_negative_numbers_and_normal_text_are_not_mangled(client):
    Item.objects.create(sku="OK-1", what_is_it="Laptop - spare parts", price="12.50")
    rows = _csv(client.get("/exports/inventory.csv"))
    row = dict(zip(rows[0], rows[1]))
    assert row["What is it?"] == "Laptop - spare parts" and row["Price"] == "12.50" and row["SKU"] == "OK-1"


# ── SQL / search injection ───────────────────────────────────────────────────
def test_search_wildcards_and_quotes_are_matched_literally(client):
    Item.objects.create(sku="A-1", what_is_it="Laptop")
    Item.objects.create(sku="B-1", what_is_it="100% tested")
    for query, expected in (("%", {"B-1"}), ("_", set()), ("' OR 1=1 --", set()), ('"; DROP TABLE x; --', set())):
        found = {i.sku for i in filter_items(ItemFilters.from_query({"q": query}))}
        assert found == expected, query
        assert client.get("/lookup/", {"q": query}).status_code == 200
        assert client.get("/api/suggestions/", {"q": query}).status_code == 200
        assert client.get("/api/palette/", {"q": query}).status_code == 200
    assert Item.objects.count() == 2
    assert escape_like("50%_\\") == "50\\%\\_\\\\"


def test_odd_skus_in_the_address_are_harmless(client):
    for sku in ("' OR '1'='1", "..", "%2e%2e", "A;DROP TABLE inventory_item", "x" * 500):
        for url in (f"/api/items/{sku}/", f"/m/{sku}/", f"/print/{sku}/", f"/api/scripts/{sku}/"):
            assert client.get(url).status_code in (200, 404), url
    assert client.get("/lookup/").status_code == 200


# ── uploads ──────────────────────────────────────────────────────────────────
def test_files_pretending_to_be_photos_are_refused(client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    fakes = [
        SimpleUploadedFile("shell.php.jpg", b"<?php system($_GET['c']); ?>", content_type="image/jpeg"),
        SimpleUploadedFile("x.svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', content_type="image/svg+xml"),
        SimpleUploadedFile("x.html", b"<html><script>alert(1)</script></html>", content_type="image/png"),
        SimpleUploadedFile("empty.png", b"", content_type="image/png"),
    ]
    for fake in fakes:
        response = client.post("/api/photos/upload/", {"sku": "UP-1", "photo": fake})
        assert response.status_code == 400, fake.name
    assert not Photo.objects.exists()


def test_other_image_formats_are_refused(client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    for fmt, ext in (("BMP", "bmp"), ("TIFF", "tif"), ("ICO", "ico")):
        buffer = io.BytesIO()
        Image.new("RGB", (20, 20)).save(buffer, fmt)
        upload = SimpleUploadedFile(f"x.{ext}", buffer.getvalue(), content_type="image/png")
        assert client.post("/api/photos/upload/", {"sku": "UP-1", "photo": upload}).status_code == 400
    assert not Photo.objects.exists()


def test_decompression_bomb_is_refused_without_crashing(client, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)  # anything over ~2000 pixels counts as a bomb
    response = client.post("/api/photos/upload/", {"sku": "UP-1", "photo": make_image(size=(200, 200))})
    assert response.status_code == 400 and not Photo.objects.exists()


def test_photo_filenames_cannot_escape_the_photo_folder(client, settings):
    from pathlib import Path

    upload = make_image(name="../../../../evil.jpg")
    assert client.post("/api/photos/upload/", {"sku": "../../etc", "photo": upload}).status_code == 200
    photo = Photo.objects.get()
    media = Path(settings.MEDIA_ROOT).resolve()
    from inventory.services.photos import photo_path
    assert media in photo_path(photo).resolve().parents
    assert "/" not in photo.stored_name and "\\" not in photo.stored_name and ".." not in photo.stored_name


def test_saved_photos_are_reencoded_so_hidden_data_is_dropped(client):
    buffer = io.BytesIO()
    Image.new("RGB", (600, 400), (10, 20, 30)).save(buffer, "JPEG")
    payload = buffer.getvalue() + b"<?php echo 'hidden'; ?>"
    from django.core.files.uploadedfile import SimpleUploadedFile
    client.post("/api/photos/upload/", {"sku": "UP-1", "photo": SimpleUploadedFile("a.jpg", payload, content_type="image/jpeg")})
    from inventory.services.photos import photo_path
    assert b"<?php" not in photo_path(Photo.objects.get()).read_bytes()


def test_doc_photos_must_be_real_images(client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client.cookies[NAME_COOKIE] = "Ann"
    client.post("/docs/new/", {"title": "Bad", "photos": [SimpleUploadedFile("x.png", b"not an image")]})
    assert not DocPost.objects.exists()


def test_imaging_upload_refuses_oversized_and_broken_files(client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from imaging.models import DeviceReport
    big = SimpleUploadedFile("big.json", b"[" + b" " * (1024 * 1024 + 10) + b"]")
    broken = SimpleUploadedFile("bad.json", b"{not json")
    too_many = SimpleUploadedFile("many.json", json.dumps([{"serial": f"S{n}"} for n in range(101)]).encode())
    client.post("/imaging/upload/", {"files": [big, broken, too_many]})
    assert not DeviceReport.objects.exists()


def test_database_import_from_upload_name_cannot_pick_the_path(client, tmp_path):
    from django.core.files.uploadedfile import SimpleUploadedFile

    response = client.post("/api/ops/import-database/",
                           {"file": SimpleUploadedFile("../../../pinksheet.sqlite3", b"not a database")})
    assert response.status_code == 400 and Item.objects.count() == 0


# ── serving files ────────────────────────────────────────────────────────────
def test_listing_image_files_cannot_read_outside_their_folder(client, settings):
    from pathlib import Path

    secret = Path(settings.MEDIA_ROOT) / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    (Path(settings.DATA_DIR) / "secret_key.txt").write_text("key", encoding="utf-8")
    for sku, name in (("..", "secret.txt"), ("ABC", "..%2F..%2Fsecret.txt"), ("..%2F..", "secret_key.txt"),
                      ("ABC", "....//....//secret.txt"), (".", "secret.txt")):
        response = client.get(f"/listing-images/files/{sku}/{name}")
        assert response.status_code == 404, (sku, name)
    assert sku_directory("../../etc") == "ETC" and sku_directory("..") == "UNASSIGNED"
    assert sanitize_filename("../../a b.png") == "a_b.png"


def test_missing_photos_are_404_not_errors(client):
    assert client.get("/photos/999/").status_code == 404
    assert client.get("/docs/photos/999/").status_code == 404


def test_photo_download_name_cannot_inject_headers(client):
    client.post("/api/photos/upload/", {"sku": "UP-1", "photo": make_image(name='a"\r\nSet-Cookie: x=1.jpg')})
    photo = Photo.objects.get()
    response = client.get(f"/photos/{photo.pk}/?download=1")
    assert response.status_code == 200
    assert "\n" not in response["Content-Disposition"] and "Set-Cookie" not in str(response.cookies)


def test_old_php_links_only_redirect_inside_the_app(client):
    for url in ("/card.php?sku=//evil.example", "/mobile_action.php?sku=https://evil.example",
                "/lookup.php?next=https://evil.example", "/photo.php?id=1&thumb=//evil.example"):
        response = client.get(url)
        assert response.status_code in (301, 404)
        if response.status_code == 301:
            assert response["Location"].startswith("/"), url
            assert not response["Location"].startswith("//"), url


def test_qr_card_redirect_stays_in_the_app(client):
    for sku in ("//evil.example", "https:evil.example", "x\r\nLocation: http://evil.example"):
        response = client.get(f"/card/{sku}/")
        if response.status_code == 302:
            assert response["Location"].startswith("/") and not response["Location"].startswith("//")


# ── security headers ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", ["/", "/login/", "/api/health/", "/docs/", "/nope/"])
def test_security_headers_on_every_response(client, url):
    response = client.get(url)
    csp = response["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-eval" not in csp
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'self'" in csp and "object-src" not in csp or "object-src 'none'" in csp
    assert response["X-Frame-Options"] == "SAMEORIGIN"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "microphone=()" in response["Permissions-Policy"]


def test_error_pages_do_not_leak_debug_details(client, settings):
    assert settings.DEBUG is False
    html = client.get("/definitely-not-a-page/").content.decode()
    assert "Traceback" not in html and "urlpatterns" not in html and "SECRET_KEY" not in html


def test_debug_is_off_unless_asked_for():
    from pinksheet.env import env_bool

    import os
    os.environ.pop("DJANGO_DEBUG", None)
    assert env_bool("DJANGO_DEBUG", False) is False


def test_secret_key_is_not_the_django_default(settings):
    assert settings.SECRET_KEY and "django-insecure" not in settings.SECRET_KEY


def test_system_page_never_shows_secrets(client, settings):
    settings.SQUARE = {**settings.SQUARE, "ACCESS_TOKEN": "sq-secret-token-123", "LOCATION_ID": "L1",
                       "WEBHOOK_SIGNATURE_KEY": "sig-secret-456"}
    settings.PINKSHEET = {**settings.PINKSHEET, "IMAGING_API_KEY": "imaging-secret-789"}
    html = client.get("/system/").content.decode()
    html += json.dumps(client.get("/api/square/status/").json()) + json.dumps(client.get("/api/health/").json())
    for secret in ("sq-secret-token-123", "sig-secret-456", "imaging-secret-789", settings.SECRET_KEY):
        assert secret not in html


# ── Square webhook ───────────────────────────────────────────────────────────
def _sign(body: bytes, key="sig-key", url="https://shop.example.com/webhooks/square/"):
    return base64.b64encode(hmac.new(key.encode(), url.encode() + body, hashlib.sha256).digest()).decode()


def _hook(client, body: bytes, signature=None, **extra):
    return client.post("/webhooks/square/", body, content_type="application/json",
                       HTTP_X_SQUARE_HMACSHA256_SIGNATURE=_sign(body) if signature is None else signature, **extra)


def test_webhook_signature_must_match_the_key_url_and_body(client, square_settings):
    body = json.dumps({"type": "test.webhook", "event_id": "e1"}).encode()
    assert _hook(client, body).status_code == 200
    assert _hook(client, body, signature=_sign(body, key="wrong")).status_code == 401
    assert _hook(client, body, signature=_sign(body, url="https://evil.example/")).status_code == 401
    assert _hook(client, body + b" ", signature=_sign(body)).status_code == 401
    assert _hook(client, body, signature="").status_code == 401


def test_webhook_refuses_gets_huge_bodies_and_junk(client, square_settings):
    assert client.get("/webhooks/square/").status_code == 405
    big = b"{" + b" " * (1024 * 1024 + 1) + b"}"
    assert _hook(client, big).status_code == 413
    for junk in (b"[1, 2]", b"not json", b"\xff\xfe"):
        assert _hook(client, junk).status_code == 400


def test_webhook_replays_are_ignored(client, square_settings):
    from tests.test_square import order_event

    Item.objects.create(sku="WH-1", what_is_it="Laptop")
    from squaresync.models import CatalogSync
    CatalogSync.objects.create(sku_normalized="WH-1", square_variation_id="VAR-1", square_item_id="ITEM-1")
    body = json.dumps(order_event(sku="WH-1")).encode()
    first, second = _hook(client, body), _hook(client, body)
    assert first.status_code == 200 and second.status_code == 200
    from squaresync.models import Sale
    assert Sale.objects.count() == 1


def test_webhook_from_the_far_past_or_future_is_refused(client, square_settings):
    for when in ("2001-01-01T00:00:00Z", "2099-01-01T00:00:00Z", "not a date"):
        body = json.dumps({"type": "payment.updated", "event_id": "old", "created_at": when}).encode()
        assert _hook(client, body).status_code == 401, when
    assert not WebhookEvent.objects.exists()


# ── imaging API key ──────────────────────────────────────────────────────────
def test_imaging_key_must_match_exactly(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "IMAGING_API_KEY": "secret"}
    body = json.dumps({"serial": "S1"})
    for headers in ({}, {"HTTP_AUTHORIZATION": "Bearer "}, {"HTTP_AUTHORIZATION": "Bearer secre"},
                    {"HTTP_AUTHORIZATION": "Bearer secret2"}, {"HTTP_AUTHORIZATION": "Basic secret"},
                    {"HTTP_X_API_KEY": "SECRET"}):
        assert client.post("/api/imaging/report/", body, content_type="application/json", **headers).status_code == 401
    assert client.post("/api/imaging/report/", body, content_type="application/json",
                       HTTP_X_API_KEY="secret").status_code == 200


def test_imaging_api_refuses_huge_bodies_and_is_off_in_the_demo(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "IMAGING_API_KEY": "secret"}
    big = json.dumps([{"serial": "S", "pad": "x" * (1024 * 1024)}])
    assert client.post("/api/imaging/report/", big, content_type="application/json",
                       HTTP_X_API_KEY="secret").status_code == 413
    settings.PINKSHEET = {**settings.PINKSHEET, "DEMO_MODE": True}
    assert client.post("/api/imaging/report/", "{}", content_type="application/json",
                       HTTP_X_API_KEY="secret").status_code == 403


# ── QZ Tray signing ──────────────────────────────────────────────────────────
def test_qz_sign_refuses_other_sites_and_oversized_requests(client, settings, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (tmp_path / "private-key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    settings.PINKSHEET = {**settings.PINKSHEET, "QZ_SIGNING_DIR": tmp_path}
    sign = lambda body, origin="http://testserver": client.post(  # noqa: E731
        "/qz/sign/", json.dumps(body), content_type="application/json", HTTP_ORIGIN=origin)
    for origin in ("http://testserver.evil.example", "null", "http://evil.example"):
        assert sign({"request": "x"}, origin).status_code == 403
    assert sign({"request": "x" * (64 * 1024 + 1)}).status_code == 400
    assert sign({"request": ["not", "text"]}).status_code == 400
    assert client.get("/qz/sign/").status_code == 405


# ── abuse limits ─────────────────────────────────────────────────────────────
def test_bug_reports_and_doc_posts_are_length_limited(client):
    from operations.models import BugReport

    client.post("/report-bug/", {"name": "n" * 500, "summary": "s" * 5000, "details": "d" * 100000, "page": "p" * 5000})
    report = BugReport.objects.get()
    assert len(report.reporter) <= 80 and len(report.summary) <= 200
    assert len(report.details) <= 5000 and len(report.page) <= 500
    client.cookies[NAME_COOKIE] = "Ann"
    client.post("/docs/new/", {"title": "t" * 1000, "body": "b" * 100000, "category": "c" * 500})
    post = DocPost.objects.get()
    assert len(post.title) <= 200 and len(post.body) <= 50000 and len(post.category) <= 40


def test_layout_and_draft_endpoints_cope_with_garbage(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "LISTING_IMAGES": True}
    for body in (b"[]", b"null", b"\xff", b'{"positions": "x"}', b'{"data": [1]}'):
        assert client.post("/api/listing-images/ABC/layout/", body, content_type="application/json").status_code == 400
        assert client.post("/api/drafts/ABC/", body, content_type="application/json").status_code in (200, 400)
    for bad in ("abc", "nan", "inf", [1]):
        response = client.post("/api/listing-images/ABC/layout/", {"positions": [{"x": bad}]},
                               content_type="application/json")
        assert response.status_code == 400, bad


def test_photo_reorder_with_bad_ids_is_refused(client):
    for ids in (["a", "b"], [1], "1,2", [{"$gt": 0}, 2]):
        assert client.post("/api/photos/reorder/", {"ids": ids}, content_type="application/json").status_code == 400


def test_demo_mode_blocks_operator_and_upload_actions_even_from_localhost(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "DEMO_MODE": True}
    for url in ("/api/ops/backup/", "/api/ops/import-database/", "/api/photos/upload/", "/api/square/sync-all/"):
        assert client.post(url).status_code == 403, url
