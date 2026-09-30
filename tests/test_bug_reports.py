"""The "Report a bug" button: reports are saved and listed under Bug reports in the admin."""
import re

import pytest

from operations.models import BugReport

pytestmark = pytest.mark.django_db
URL = "/report-bug/"
ADMIN = "/admin/operations/bugreport/"


def test_sidebar_button_links_to_the_form_from_the_current_page(client):
    html = client.get("/lookup/?q=dell").content.decode()
    assert "Report a bug" in html and 'href="/report-bug/?from=/lookup/%3Fq%3Ddell"' in html


def test_form_renders_with_the_page_it_came_from(client):
    html = client.get(URL + "?from=/board/").content.decode()
    assert 'name="summary"' in html and 'value="/board/"' in html


def test_report_is_saved_with_who_sent_it(client, django_user_model):
    client.force_login(django_user_model.objects.create_user("sam", password="x", first_name="Sam"))
    page = client.post(URL, {
        "name": "Sam Lee", "summary": "Print label does nothing", "details": "Clicked Print on LAP-1", "page": "/intake/?sku=LAP-1",
    }, HTTP_USER_AGENT="TestBrowser/1.0", follow=True)
    assert "Your bug report was sent" in page.content.decode()
    report = BugReport.objects.get()
    assert report.reporter == "Sam Lee" and report.sent_by == "Sam"
    assert report.summary == "Print label does nothing"
    assert report.details == "Clicked Print on LAP-1" and report.page == "/intake/?sku=LAP-1"
    assert report.browser == "TestBrowser/1.0"


def test_name_is_filled_in_when_dispodex_knows_it(client, django_user_model):
    assert re.search(r'id="bug-name"[^>]*value=""', client.get(URL).content.decode())
    client.cookies["ps_name"] = "Front desk"
    assert 'value="Front desk"' in client.get(URL).content.decode()
    client.force_login(django_user_model.objects.create_user("sam", password="x", first_name="Sam"))
    assert 'value="Sam"' in client.get(URL).content.decode()


def test_name_is_required(client):
    page = client.post(URL, {"name": "  ", "summary": "Search is slow"})
    assert page.status_code == 200 and "Add your name" in page.content.decode()
    assert 'value="Search is slow"' in page.content.decode()  # what they typed is kept
    assert not BugReport.objects.exists()


def test_device_that_sent_it_is_recorded_without_sign_in(client):
    client.post(URL, {"name": "Jo", "summary": "Search is slow"}, REMOTE_ADDR="192.168.1.20")
    report = BugReport.objects.get()
    assert report.reporter == "Jo" and report.sent_by == "Device 192.168.1.20"


def test_summary_is_required_and_what_they_typed_is_kept(client):
    page = client.post(URL, {"name": "Jo", "summary": "   ", "details": "something"})
    assert page.status_code == 200 and "Say what went wrong" in page.content.decode()
    assert "something</textarea>" in page.content.decode()
    assert not BugReport.objects.exists()


def test_name_and_summary_are_one_tidy_line(client):
    client.post(URL, {"name": " Jo \n Smith ", "summary": "Broken\r\n   again"})
    report = BugReport.objects.get()
    assert report.summary == "Broken again" and report.reporter == "Jo Smith"


def test_sign_in_is_needed_when_login_is_on(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "REQUIRE_LOGIN": True}
    assert client.get(URL)["Location"].startswith("/login/")


def test_turned_off_in_the_demo(client, settings):
    settings.PINKSHEET = {**settings.PINKSHEET, "DEMO_MODE": True}
    page = client.post(URL, {"name": "x", "summary": "spam"})
    assert "turned off in the demo" in page.content.decode()
    assert not BugReport.objects.exists()


def test_admin_has_a_bug_reports_section(client, django_user_model):
    BugReport.objects.create(summary="Search is slow", reporter="Sam", details="Takes 10 seconds")
    client.force_login(django_user_model.objects.create_superuser("owner", password="x"))
    assert "Bug reports" in client.get("/admin/").content.decode()
    assert "Search is slow" in client.get(ADMIN).content.decode()
    report = BugReport.objects.get()
    assert "Takes 10 seconds" in client.get(f"{ADMIN}{report.pk}/change/").content.decode()


def test_reports_are_read_only_but_can_be_cleared(client, django_user_model):
    report = BugReport.objects.create(summary="Fixed now", reporter="Sam")
    client.force_login(django_user_model.objects.create_superuser("owner", password="x"))
    assert client.get(ADMIN + "add/").status_code == 403
    client.post(f"{ADMIN}{report.pk}/change/", {"summary": "Changed"})
    assert BugReport.objects.get().summary == "Fixed now"
    client.post(f"{ADMIN}{report.pk}/delete/", {"post": "yes"})
    assert not BugReport.objects.exists()
