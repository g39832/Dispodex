"""Team docs: a shared page of posts, each with a title, the details and photos underneath."""
import mimetypes

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.actor import clean_name
from inventory.services.photos import PhotoError
from inventory.views.photos import _cached_file_response
from teamdocs import photos as doc_photos
from teamdocs.models import DocPhoto, DocPost

MAX_BODY = 50000


def _can_edit(request, post: DocPost) -> bool:
    """Signed in: the author or staff. Without sign-in, anyone (every edit is still named)."""
    user = request.user
    if not user.is_authenticated:
        return not settings.PINKSHEET["REQUIRE_LOGIN"]
    return user.is_staff or post.author_user_id == user.pk


def _asks_for_name(request) -> bool:
    return not (request.user.is_authenticated or request.actor_named)


def _read_form(request):
    """(form values, photo uploads, who is posting, error message)."""
    form = {key: request.POST.get(key, "").strip() for key in ("name", "title", "body")}
    uploads = [f for f in request.FILES.getlist("photos") if f.size]
    # The signed-in account or saved device name, else the name typed on the form.
    who = clean_name(form["name"]) if _asks_for_name(request) else request.actor[:80]
    if not who:
        return form, uploads, who, "Add your name so everyone knows who posted it."
    if not form["title"]:
        return form, uploads, who, "Give the post a title."
    return form, uploads, who, ""


def doc_list(request):
    query = request.GET.get("q", "").strip()
    posts = DocPost.objects.annotate(photo_count=Count("photos")).order_by("-created_at", "-id")
    if query:
        posts = posts.filter(Q(title__icontains=query) | Q(body__icontains=query) | Q(author__icontains=query))
    page_obj = Paginator(posts, 20).get_page(request.GET.get("page"))
    covers = {}
    for photo in DocPhoto.objects.filter(post__in=[p.pk for p in page_obj]).order_by("post_id", "sort_order", "id"):
        covers.setdefault(photo.post_id, photo.pk)
    for post in page_obj:
        post.cover_id = covers.get(post.pk)
    return render(request, "teamdocs/list.html", {
        "page": "docs", "page_obj": page_obj, "query": query, "total": DocPost.objects.count(),
    })


def doc_detail(request, post_id: int):
    post = get_object_or_404(DocPost, pk=post_id)
    return render(request, "teamdocs/detail.html", {
        "page": "docs", "post": post, "photos": list(post.photos.all()), "can_edit": _can_edit(request, post),
    })


def doc_new(request):
    demo = settings.PINKSHEET["DEMO_MODE"]
    form = {"name": "", "title": "", "body": ""}
    error = ""
    if request.method == "POST" and not demo:
        form, uploads, author, error = _read_form(request)
        if not error:
            try:
                with transaction.atomic():
                    post = DocPost.objects.create(
                        title=" ".join(form["title"].split())[:200], body=form["body"][:MAX_BODY], author=author,
                        author_user=request.user if request.user.is_authenticated else None,
                    )
                    doc_photos.add_photos(post, uploads, author)
            except PhotoError as exc:
                error = f"{exc} Nothing was posted. Pick your photos again and resend."
            else:
                messages.success(request, "Posted.")
                return redirect("doc_detail", post_id=post.pk)
    return render(request, "teamdocs/form.html", {
        "page": "docs", "form": form, "error": error, "demo": demo, "post": None,
        "ask_name": _asks_for_name(request), "max_photos": doc_photos.MAX_PHOTOS_PER_POST,
    })


def doc_edit(request, post_id: int):
    post = get_object_or_404(DocPost, pk=post_id)
    if not _can_edit(request, post):
        messages.error(request, "Only the person who posted this, or a staff member, can edit it.")
        return redirect("doc_detail", post_id=post.pk)
    demo = settings.PINKSHEET["DEMO_MODE"]
    form = {"name": "", "title": post.title, "body": post.body}
    error = ""
    if request.method == "POST" and not demo:
        form, uploads, editor, error = _read_form(request)
        remove_ids = {int(v) for v in request.POST.getlist("remove_photo") if v.isdigit()}
        if not error:
            removed = list(post.photos.filter(pk__in=remove_ids))
            try:
                with transaction.atomic():
                    post.photos.filter(pk__in=remove_ids).delete()
                    doc_photos.add_photos(post, uploads, editor)
                    post.title = " ".join(form["title"].split())[:200]
                    post.body = form["body"][:MAX_BODY]
                    post.updated_by = editor
                    post.updated_at = timezone.now()
                    post.save()
            except PhotoError as exc:
                error = f"{exc} Nothing was changed. Pick your photos again and resend."
            else:
                for photo in removed:
                    doc_photos.delete_photo_file(photo)
                messages.success(request, "Changes saved.")
                return redirect("doc_detail", post_id=post.pk)
    return render(request, "teamdocs/form.html", {
        "page": "docs", "form": form, "error": error, "demo": demo, "post": post, "photos": list(post.photos.all()),
        "ask_name": _asks_for_name(request), "max_photos": doc_photos.MAX_PHOTOS_PER_POST,
    })


@require_POST
def doc_delete(request, post_id: int):
    post = get_object_or_404(DocPost, pk=post_id)
    if settings.PINKSHEET["DEMO_MODE"] or not _can_edit(request, post):
        messages.error(request, "Only the person who posted this, or a staff member, can delete it.")
        return redirect("doc_detail", post_id=post.pk)
    title = post.title
    post.delete()
    doc_photos.delete_post_files(post_id)
    messages.success(request, f"Deleted “{title}”.")
    return redirect("docs")


@require_GET
def doc_photo(request, photo_id: int):
    photo = get_object_or_404(DocPhoto, pk=photo_id)
    path = doc_photos.photo_path(photo)
    if not path.exists():
        raise Http404("Photo file is missing")
    content_type = photo.mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return _cached_file_response(request, path, content_type, max_age=7 * 86400)
