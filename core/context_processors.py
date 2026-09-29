from django.conf import settings

from squaresync.config import get_config

NAV = [
    ("dashboard", "Dashboard", "home"),
    ("intake", "New intake", "plus"),
    ("board", "Status board", "board"),
    ("lookup", "Lookup", "search"),
    ("archive", "Archive", "archive"),
    ("scripts", "Script builder", "script"),
    ("listing_images", "Listing images", "image"),
]


def app_context(request):
    listing_images = settings.PINKSHEET["LISTING_IMAGES"]
    return {
        "business_name": settings.PINKSHEET["BUSINESS_NAME"],
        "nav_items": [entry for entry in NAV if listing_images or entry[0] != "listing_images"],
        "listing_images_enabled": listing_images,
        "square_enabled": get_config().enabled,
        "require_login": settings.PINKSHEET["REQUIRE_LOGIN"],
        "demo_mode": settings.PINKSHEET["DEMO_MODE"],
    }
