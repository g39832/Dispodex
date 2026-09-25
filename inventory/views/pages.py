"""Status board, script builder, listing-image composer, phone page and print card."""
from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from core.skus import normalize_sku
from inventory.models import Item, ListingImageLayout, Photo, Status
from inventory.services import history
from inventory.services.scripts import final_boilerplate

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
        {"page": "scripts", "sku": sku, "recent_skus": recent_skus(), "boilerplate": final_boilerplate()},
    )


def listing_images(request):
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
