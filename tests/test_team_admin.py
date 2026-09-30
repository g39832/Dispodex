"""Staff can manage the team's accounts from "Manage users", within limits."""
import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db
User = get_user_model()
USERS = "/admin/auth/user/"


@pytest.fixture
def owner():
    return User.objects.create_superuser("owner", password="owner-pass-123")


@pytest.fixture
def lead():
    return User.objects.create_user("lead", password="lead-pass-123", is_staff=True)


@pytest.fixture
def worker():
    return User.objects.create_user("worker", password="worker-pass-123", first_name="Sam")


def change_form(user, **changes):
    data = {
        "username": user.username, "first_name": user.first_name, "last_name": user.last_name,
        "email": user.email, "is_active": "on" if user.is_active else "",
        "is_staff": "on" if user.is_staff else "", "is_superuser": "on" if user.is_superuser else "",
        "last_login_0": "", "last_login_1": "",
        "date_joined_0": user.date_joined.strftime("%Y-%m-%d"),
        "date_joined_1": user.date_joined.strftime("%H:%M:%S"),
    }
    data.update(changes)
    return {k: v for k, v in data.items() if v != ""}


def test_staff_signs_in_to_admin_and_sees_the_users_list(client, lead, worker, owner):
    page = client.post("/admin/login/?next=" + USERS, {"username": "lead", "password": "lead-pass-123", "next": USERS})
    assert page.status_code == 302 and page["Location"] == USERS
    listing = client.get(USERS).content.decode()
    assert "worker" in listing and "owner" in listing


def test_regular_staff_member_cannot_open_admin(client, worker):
    client.force_login(worker)
    assert client.get(USERS).status_code == 302  # sent to the admin sign-in


def test_staff_adds_a_person_who_can_then_sign_in(client, lead):
    client.force_login(lead)
    page = client.post(USERS + "add/", {
        "username": "newhire", "usable_password": "true",
        "password1": "Fresh-start-2026", "password2": "Fresh-start-2026",
    })
    assert page.status_code == 302, page.content.decode()[:500]
    new = User.objects.get(username="newhire")
    assert not new.is_staff and not new.is_superuser
    client.logout()
    assert client.post("/login/", {"username": "newhire", "password": "Fresh-start-2026"})["Location"] == "/"


def test_staff_renames_and_turns_off_a_worker(client, lead, worker):
    client.force_login(lead)
    page = client.post(f"{USERS}{worker.pk}/change/", change_form(worker, first_name="Samuel", is_active=""))
    assert page.status_code == 302, page.content.decode()[:500]
    worker.refresh_from_db()
    assert worker.first_name == "Samuel" and not worker.is_active


def test_staff_can_make_someone_else_staff(client, lead, worker):
    client.force_login(lead)
    client.post(f"{USERS}{worker.pk}/change/", change_form(worker, is_staff="on"))
    worker.refresh_from_db()
    assert worker.is_staff


def test_staff_resets_a_workers_password(client, lead, worker):
    client.force_login(lead)
    page = client.post(f"{USERS}{worker.pk}/password/", {
        "usable_password": "true", "password1": "Brand-new-pass-9", "password2": "Brand-new-pass-9",
    })
    assert page.status_code == 302, page.content.decode()[:500]
    worker.refresh_from_db()
    assert worker.check_password("Brand-new-pass-9")


def test_staff_cannot_make_anyone_a_superuser(client, lead, worker):
    client.force_login(lead)
    client.post(f"{USERS}{worker.pk}/change/", change_form(worker, is_superuser="on"))
    client.post(f"{USERS}{lead.pk}/change/", change_form(lead, is_superuser="on"))
    worker.refresh_from_db()
    lead.refresh_from_db()
    assert not worker.is_superuser and not lead.is_superuser


def test_staff_can_only_look_at_a_superuser(client, lead, owner):
    client.force_login(lead)
    assert client.get(f"{USERS}{owner.pk}/change/").status_code == 200  # read-only view
    client.post(f"{USERS}{owner.pk}/change/", change_form(owner, is_active="", first_name="Hacked"))
    assert client.post(f"{USERS}{owner.pk}/password/", {
        "usable_password": "true", "password1": "Taken-over-123", "password2": "Taken-over-123",
    }).status_code == 403
    assert client.post(f"{USERS}{owner.pk}/delete/", {"post": "yes"}).status_code == 403
    client.post(USERS, {"action": "delete_selected", "_selected_action": [owner.pk], "post": "yes"})
    owner.refresh_from_db()
    assert owner.is_active and owner.first_name != "Hacked" and owner.check_password("owner-pass-123")


def test_superuser_still_has_full_control(client, owner, lead):
    client.force_login(owner)
    client.post(f"{USERS}{lead.pk}/change/", change_form(lead, is_superuser="on"))
    lead.refresh_from_db()
    assert lead.is_superuser


def test_staff_do_not_get_the_rest_of_the_admin(client, lead):
    client.force_login(lead)
    assert client.get("/admin/inventory/item/").status_code == 403
    assert client.get("/admin/auth/group/").status_code == 403


@pytest.mark.parametrize("url", ["/admin/login/", USERS])
def test_admin_pages_use_the_dispodex_theme(client, lead, url):
    if url != "/admin/login/":
        client.force_login(lead)
    html = client.get(url).content.decode()
    assert "css/tokens.css" in html and "css/admin.css" in html and "js/theme-init.js" in html
    assert "admin/css/dark_mode.css" not in html  # the app's dark mode, not Django's own
    assert "Dispodex admin" in html and "img/logo.svg" in html and "data-theme-toggle" in html


def test_app_pages_still_load_the_theme_tokens(client):
    html = client.get("/").content.decode()
    assert html.index("css/tokens.css") < html.index("css/app.css")
