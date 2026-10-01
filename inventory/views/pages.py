"""Status board, script builder, listing-image composer, phone page and print card."""
from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from core.network import is_private_request
from core.skus import normalize_sku
from inventory.models import Item, ListingImageLayout, Photo, Status
from inventory.services import history
from inventory.services import scripts as script_service
from inventory.services.scripts import using_example_boilerplate
from operations.models import SystemState

MOBILE_UA_MARKERS = ("android", "iphone", "ipad", "ipod", "mobile", "opera mini", "iemobile", "silk", "blackberry", "windows phone")


def recent_skus(limit: int = 60) -> list[str]:
    seen, result = set(), []
    for sku in Item.objects.exclude(sku="").order_by("-updated_at", "-id").values_list("sku", flat=True)[: limit * 2]:
        if sku not in seen:
            seen.add(sku)
            result.append(sku)
        if len(result) >= limit:
            break
    return result


def board(request):
    return render(
        request,
        "inventory/board.html",
        {"page": "board", "lanes": Status.choices, "highlight": normalize_sku(request.GET.get("highlight"))},
    )


def script_builder(request):
    sku = normalize_sku(request.GET.get("sku") or request.GET.get("copy_sku"))
    return render(
        request,
        "inventory/scripts.html",
        {"page": "scripts", "sku": sku, "recent_skus": recent_skus(), "example_boilerplate": using_example_boilerplate()},
    )


LISTING_NOTES_EDITED_KEY = "ebay_listing_notes_edited"


def _can_edit_listing_notes(request) -> bool:
    """Staff when signed in; without sign-in, anyone on the shop network. Never in the demo."""
    if settings.PINKSHEET["DEMO_MODE"]:
        return False
    if request.user.is_authenticated:
        return request.user.is_staff
    return not settings.PINKSHEET["REQUIRE_LOGIN"] and is_private_request(request)


def listing_notes(request):
    """View and edit the shop's eBay listing notes (data/ebay_boilerplate.txt) from the browser."""
    can_edit = _can_edit_listing_notes(request)
    example = using_example_boilerplate()
    text = "" if example else script_service.final_boilerplate()
    error = ""
    if request.method == "POST":
        if not can_edit:
            messages.error(request, "Only staff can change the listing notes.")
            return redirect("listing_notes")
        text = request.POST.get("text", "")
        if not text.strip():
            error = "The listing notes can't be empty."
        elif len(text) > script_service.MAX_BOILERPLATE:
            error = f"That's longer than {script_service.MAX_BOILERPLATE:,} characters."
        else:
            script_service.save_boilerplate(text)
            SystemState.set(LISTING_NOTES_EDITED_KEY, f"{request.actor} · {timezone.localtime():%b %d, %Y %I:%M %p}")
            messages.success(request, "Listing notes saved. New prompts and descriptions use them right away.")
            return redirect("listing_notes")
    return render(request, "inventory/listing_notes.html", {
        "page": "scripts", "text": text, "example": example, "can_edit": can_edit, "error": error,
        "last_edit": SystemState.get(LISTING_NOTES_EDITED_KEY), "max_length": script_service.MAX_BOILERPLATE,
    })


def listing_images(request):
    if not settings.PINKSHEET["LISTING_IMAGES"]:
        raise Http404("Listing images is turned off for now.")
    sku = normalize_sku(request.GET.get("sku"))
    layout = ListingImageLayout.objects.filter(sku_normalized=sku).first() if sku else None
    photos = list(Photo.objects.filter(sku_normalized=sku)[:50]) if sku else []
    return render(
        request,
        "inventory/listing_images.html",
        {
            "page": "listing_images",
            "sku": sku,
            "recent_skus": recent_skus(),
            "photos": photos,
            "positions": layout.positions if layout else [],
        },
    )


def card_redirect(request, sku: str = ""):
    """Where every printed QR code points: phones get the quick card, computers get intake."""
    sku = normalize_sku(sku or request.GET.get("sku"))
    if not sku:
        raise Http404("Missing SKU")
    agent = (request.META.get("HTTP_USER_AGENT") or "").lower()
    is_mobile = any(marker in agent for marker in MOBILE_UA_MARKERS) or request.META.get("HTTP_SEC_CH_UA_MOBILE") == "?1"
    if is_mobile:
        return HttpResponseRedirect(reverse("mobile_card", args=[sku]))
    return HttpResponseRedirect(f"{reverse('intake')}?sku={sku}")


def mobile_card(request, sku: str):
    sku = normalize_sku(sku)
    item = Item.objects.filter(sku_normalized=sku).first()
    if item is None:
        return render(request, "inventory/mobile_missing.html", {"sku": sku}, status=404)
    photos = list(Photo.objects.filter(sku_normalized=sku)[:20])
    return render(
        request,
        "inventory/mobile.html",
        {"item": item, "photos": photos, "statuses": Status.choices, "history": history.recent_for(item, 8),
         "photo_limit_mb": settings.PINKSHEET["PHOTO_MAX_BYTES"] // (1024 * 1024)},
    )


def print_card(request, sku: str):
    sku = normalize_sku(sku)
    item = Item.objects.filter(sku_normalized=sku).first()
    if item is None:
        raise Http404("Item not found")
    photos = list(Photo.objects.filter(sku_normalized=sku).order_by("-is_thumb", "sort_order", "id")[:4])
    return render(
        request,
        "inventory/print_card.html",
        {
            "item": item,
            "thumb": photos[0] if photos else None,
            "extra_photos": photos[1:],
            "qr_url": request.build_absolute_uri(reverse("card", args=[sku])),
            "printed_at": timezone.localtime(),
            "autoprint": request.GET.get("autoprint") == "1",
        },
    )
