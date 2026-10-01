"""Team docs: posts with a title, details and photos, each showing who wrote it."""
import pytest

from teamdocs import photos as doc_photos
from teamdocs.models import DocPhoto, DocPost

pytestmark = pytest.mark.django_db


@pytest.fixture
def sam(django_user_model):
    return django_user_model.objects.create_user("sam", password="x", first_name="Sam")


def _post(client, image=None, **fields):
    data = {"title": "New shipping cutoff", "body": "Orders after 2pm ship tomorrow.", **fields}
    return client.post("/docs/new/", data, follow=True)


def test_sidebar_links_to_team_docs(client):
    html = client.get("/").content.decode()
    assert 'href="/docs/"' in html and "Team docs" in html


def test_empty_page_invites_the_first_post(client):
    html = client.get("/docs/").content.decode()
    assert "No posts yet" in html and 'href="/docs/new/"' in html


def test_post_shows_title_details_author_and_photos(client, sam, image):
    client.force_login(sam)
    page = client.post("/docs/new/", {
        "title": "  New   shipping cutoff ", "body": "Orders after 2pm ship tomorrow.\n\nSee https://example.com",
        "photos": [image(name="dock.jpg"), image("PNG", name="label.png")],
    }, follow=True)
    html = page.content.decode()
    post = DocPost.objects.get()
    assert post.title == "New shipping cutoff" and post.author == "Sam" and post.author_user == sam
    assert "Posted." in html and "<strong>Sam</strong>" in html
    assert '<a href="https://example.com"' in html  # links are clickable
    photos = list(post.photos.all())
    assert [p.original_name for p in photos] == ["dock.jpg", "label.png"]
    assert all(doc_photos.photo_path(p).exists() for p in photos)
    for photo in photos:
        assert f'/docs/photos/{photo.pk}/' in html
        response = client.get(f"/docs/photos/{photo.pk}/")
        assert response.status_code == 200 and response["Content-Type"].startswith("image/")


def test_list_shows_newest_first_with_author_and_photo_count(client, sam, image):
    client.force_login(sam)
    client.post("/docs/new/", {"title": "Older post", "body": "x"})
    client.post("/docs/new/", {"title": "Newer post", "body": "y", "photos": [image()]})
    html = client.get("/docs/").content.decode()
    assert html.index("Newer post") < html.index("Older post")
    assert "1 photo" in html and "Sam" in html


def test_search_matches_title_body_and_author(client, sam):
    client.force_login(sam)
    client.post("/docs/new/", {"title": "Label printer", "body": "Restart QZ Tray first"})
    client.post("/docs/new/", {"title": "Holiday hours", "body": "Closed Monday"})
    assert "Holiday hours" not in client.get("/docs/?q=qz tray").content.decode()
    assert "Label printer" in client.get("/docs/?q=qz tray").content.decode()
    assert "Holiday hours" in client.get("/docs/?q=sam").content.decode()


def test_title_is_required_and_typing_is_kept(client, sam):
    client.force_login(sam)
    page = client.post("/docs/new/", {"title": "  ", "body": "kept text"})
    assert page.status_code == 200 and "Give the post a title" in page.content.decode()
    assert "kept text</textarea>" in page.content.decode()
    assert not DocPost.objects.exists()


def test_bad_photo_posts_nothing(client, sam, image):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client.force_login(sam)
    fake = SimpleUploadedFile("notes.jpg", b"not really an image", content_type="image/jpeg")
    page = client.post("/docs/new/", {"title": "With photos", "photos": [image(), fake]})
    html = page.content.decode()
    assert "notes.jpg is not a valid image file" in html and "Nothing was posted" in html
    assert not DocPost.objects.exists() and not DocPhoto.objects.exists()
    root = doc_photos.post_dir(1).parent
    assert not root.exists() or not [p for p in root.rglob("*") if p.is_file()]


def test_without_sign_in_the_name_is_asked_for_or_taken_from_the_device(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "REQUIRE_LOGIN": False}
    assert 'name="name"' in client.get("/docs/new/").content.decode()
    page = client.post("/docs/new/", {"name": "", "title": "Hi"})
    assert "Add your name" in page.content.decode() and not DocPost.objects.exists()
    client.post("/docs/new/", {"name": "Front desk", "title": "Hi"})
    assert DocPost.objects.get().author == "Front desk"
    client.cookies["ps_name"] = "Jo"
    assert 'name="name"' not in client.get("/docs/new/").content.decode()
    client.post("/docs/new/", {"title": "From Jo"})
    assert DocPost.objects.get(title="From Jo").author == "Jo"


def test_author_edits_and_is_named_as_editor(client, sam, image):
    client.force_login(sam)
    client.post("/docs/new/", {"title": "Old title", "photos": [image(name="a.jpg"), image(name="b.jpg")]})
    post = DocPost.objects.get()
    first, second = post.photos.all()
    page = client.post(f"/docs/{post.pk}/edit/", {
        "title": "New title", "body": "More info", "remove_photo": [str(first.pk)], "photos": [image(name="c.jpg")],
    }, follow=True)
    post.refresh_from_db()
    assert post.title == "New title" and post.body == "More info"
    assert post.updated_by == "Sam" and post.updated_at and post.author == "Sam"
    assert [p.original_name for p in post.photos.all()] == ["b.jpg", "c.jpg"]
    assert not doc_photos.photo_path(first).exists()
    assert "Edited by Sam" in page.content.decode()


def test_only_the_author_or_staff_can_edit_or_delete(client, sam, django_user_model):
    client.force_login(sam)
    client.post("/docs/new/", {"title": "Sam's post"})
    post = DocPost.objects.get()

    client.force_login(django_user_model.objects.create_user("jo", password="x"))
    html = client.get(f"/docs/{post.pk}/").content.decode()
    assert f"/docs/{post.pk}/edit/" not in html
    client.post(f"/docs/{post.pk}/edit/", {"title": "Hijacked"})
    client.post(f"/docs/{post.pk}/delete/")
    post.refresh_from_db()
    assert post.title == "Sam's post"

    client.force_login(django_user_model.objects.create_user("boss", password="x", first_name="Boss", is_staff=True))
    client.post(f"/docs/{post.pk}/edit/", {"title": "Fixed typo"})
    post.refresh_from_db()
    assert post.title == "Fixed typo" and post.updated_by == "Boss" and post.author == "Sam"


def test_delete_removes_the_post_and_its_photo_files(client, sam, image):
    client.force_login(sam)
    client.post("/docs/new/", {"title": "Temp", "photos": [image()]})
    post = DocPost.objects.get()
    folder = doc_photos.post_dir(post.pk)
    assert folder.exists()
    page = client.post(f"/docs/{post.pk}/delete/", follow=True)
    assert "Deleted" in page.content.decode()
    assert not DocPost.objects.exists() and not DocPhoto.objects.exists() and not folder.exists()
    assert client.get(f"/docs/{post.pk}/").status_code == 404


def test_delete_needs_post(client, sam):
    client.force_login(sam)
    client.post("/docs/new/", {"title": "Keep"})
    post = DocPost.objects.get()
    assert client.get(f"/docs/{post.pk}/delete/").status_code == 405
    assert DocPost.objects.exists()


def test_posting_is_off_in_the_demo(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "DEMO_MODE": True}
    page = client.post("/docs/new/", {"name": "Jo", "title": "Hi"})
    assert "Posting is turned off in the demo" in page.content.decode()
    assert not DocPost.objects.exists()


def test_backups_mirror_doc_photos(client, sam, image, tmp_path):
    from operations import backups

    client.force_login(sam)
    client.post("/docs/new/", {"title": "Backed up", "photos": [image()]})
    assert backups.mirror_photos(tmp_path) == 1
    assert len([p for p in (tmp_path / "doc_photos").rglob("*") if p.is_file()]) == 1


def test_pinned_posts_sit_above_the_rest(client, sam):
    client.force_login(sam)
    for title in ("Oldest post", "Middle post", "Newest post"):
        client.post("/docs/new/", {"title": title})
    oldest = DocPost.objects.get(title="Oldest post")
    page = client.post(f"/docs/{oldest.pk}/pin/", follow=True).content.decode()
    assert "Pinned to the top" in page and "Pinned by Sam" in page and "Unpin" in page

    html = client.get("/docs/").content.decode()
    assert "Everything else" in html
    assert html.index("Oldest post") < html.index("Newest post") < html.index("Middle post")

    client.post(f"/docs/{oldest.pk}/pin/")
    oldest.refresh_from_db()
    assert oldest.pinned_at is None and oldest.pinned_by == ""
    html = client.get("/docs/").content.decode()
    assert "Everything else" not in html and html.index("Middle post") < html.index("Oldest post")


def test_only_the_author_or_staff_can_pin(client, sam, django_user_model):
    client.force_login(sam)
    client.post("/docs/new/", {"title": "Sam's post"})
    post = DocPost.objects.get()
    client.force_login(django_user_model.objects.create_user("jo", password="x"))
    assert "/pin/" not in client.get(f"/docs/{post.pk}/").content.decode()
    client.post(f"/docs/{post.pk}/pin/")
    post.refresh_from_db()
    assert post.pinned_at is None
    client.force_login(django_user_model.objects.create_user("boss", password="x", first_name="Boss", is_staff=True))
    client.post(f"/docs/{post.pk}/pin/")
    post.refresh_from_db()
    assert post.pinned_at and post.pinned_by == "Boss"
    assert client.get(f"/docs/{post.pk}/pin/").status_code == 405


def test_categories_reuse_spelling_and_filter_the_list(client, sam):
    client.force_login(sam)
    client.post("/docs/new/", {"title": "Label printer", "category": "How-to"})
    client.post("/docs/new/", {"title": "Wipe a laptop", "category": "  how-TO "})
    client.post("/docs/new/", {"title": "Holiday hours", "category": "Policies"})
    client.post("/docs/new/", {"title": "No category post"})
    assert set(DocPost.objects.values_list("category", flat=True)) == {"How-to", "Policies", ""}

    html = client.get("/docs/").content.decode()
    assert "How-to <span class=\"count\">2</span>" in html and "Policies <span class=\"count\">1</span>" in html
    html = client.get("/docs/?category=how-to").content.decode()
    assert "Label printer" in html and "Wipe a laptop" in html
    assert "Holiday hours" not in html and "No category post" not in html
    assert 'href="/docs/new/?category=how-to"' in html  # new posts start in the category being viewed
    assert "Holiday hours" in client.get("/docs/?q=polic").content.decode()  # search covers categories

    post = DocPost.objects.get(title="Holiday hours")
    client.post(f"/docs/{post.pk}/edit/", {"title": "Holiday hours", "category": ""})
    post.refresh_from_db()
    assert post.category == ""
    assert 'value="How-to"' in client.get("/docs/new/").content.decode()  # suggested on the form


def test_filter_by_who_posted_and_pages_keep_filters(client, sam, django_user_model):
    client.force_login(sam)
    for n in range(21):
        client.post("/docs/new/", {"title": f"Sam note {n}", "category": "Notes"})
    client.force_login(django_user_model.objects.create_user("jo", password="x", first_name="Jo"))
    client.post("/docs/new/", {"title": "Jo note", "category": "Notes"})

    html = client.get("/docs/?author=Jo").content.decode()
    assert "Jo note" in html and "Sam note" not in html
    html = client.get("/docs/?author=Sam&category=Notes").content.decode()
    assert "Jo note" not in html and "?category=Notes&amp;author=Sam&amp;page=2" in html
    assert "No posts match these filters" in client.get("/docs/?author=Nobody").content.decode()
