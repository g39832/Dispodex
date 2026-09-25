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
    return {
        "business_name": settings.PINKSHEET["BUSINESS_NAME"],
        "nav_items": NAV,
        "square_enabled": get_config().enabled,
        "require_login": settings.PINKSHEET["REQUIRE_LOGIN"],
    }
