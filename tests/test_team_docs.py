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
