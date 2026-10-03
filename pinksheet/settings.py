"""
Django settings for Dispodex.

Everything that changes between machines (secrets, Square credentials, paths,
ports) is read from the ``.env`` file in the project root. Copy
``.env.example`` to ``.env`` and edit that file — never edit this one to put
in a password or token.
"""
from pathlib import Path

from pinksheet.env import env, env_bool, env_int, env_list, load_env_file

BASE_DIR = Path(__file__).resolve().parent.parent
if not env_bool("PINKSHEET_SKIP_DOTENV", False):  # the test suite ignores the real .env
    load_env_file(BASE_DIR / ".env")

# ── Where runtime data lives (database, photos, backups, logs) ────────────
DATA_DIR = Path(env("PINKSHEET_DATA_DIR", str(BASE_DIR / "data"))).resolve()
MEDIA_ROOT = DATA_DIR / "media"
BACKUP_DIR = Path(env("BACKUP_DIR", str(DATA_DIR / "backups"))).resolve()
LOG_DIR = DATA_DIR / "logs"
for _dir in (DATA_DIR, MEDIA_ROOT, BACKUP_DIR, LOG_DIR):
    _dir.mkdir(parents=True, exist_ok=True)


def _secret_key() -> str:
    """Use DJANGO_SECRET_KEY from .env, or create one once and keep it in data/."""
    configured = env("DJANGO_SECRET_KEY", "")
    if configured:
        return configured
    key_file = DATA_DIR / "secret_key.txt"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    from django.core.management.utils import get_random_secret_key

    key = get_random_secret_key()
    key_file.write_text(key, encoding="utf-8")
    return key


SECRET_KEY = _secret_key()
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["*"])
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "inventory",
    "archive",
    "squaresync",
    "operations",
    "teamdocs",
    "imaging",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.actor.ActorMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
    "core.middleware.MaintenanceModeMiddleware",
    "core.middleware.LoginRequiredMiddleware",
]

ROOT_URLCONF = "pinksheet.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.app_context",
            ],
        },
    },
]

WSGI_APPLICATION = "pinksheet.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "pinksheet.sqlite3",
        "OPTIONS": {
            # WAL lets the five people using the app read while someone saves.
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            "timeout": 20,
            "transaction_mode": "IMMEDIATE",
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
# Back to Dispodex's own sign-in page, not the admin's (which only lets in admins).
LOGOUT_REDIRECT_URL = "/login/"

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("PINKSHEET_TIME_ZONE", "America/Chicago")
USE_I18N = False
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = DATA_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}
WHITENOISE_USE_FINDERS = DEBUG
MEDIA_URL = "/media/"  # photos are served by views, never directly

# Uploads: photos can be large straight off a phone camera.
DATA_UPLOAD_MAX_MEMORY_SIZE = 64 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 5000
# The largest request the web server accepts. Imports can be big (the Excel export with photos
# is often ~100 MB); bodies this size are spooled to disk, not held in memory.
MAX_REQUEST_BODY_SIZE = 1024 * 1024 * 1024

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "SAMEORIGIN"  # the board prints cards through a same-origin iframe
if env_bool("PINKSHEET_BEHIND_HTTPS_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
else:
    # Plain HTTP on the shop's private network: the HTTPS-only checks don't apply.
    # Browsers ignore this header over plain HTTP (except on localhost) and log a console
    # warning on every page, so only send it when served over HTTPS.
    SECURE_CROSS_ORIGIN_OPENER_POLICY = None
    SILENCED_SYSTEM_CHECKS = ["security.W004", "security.W008", "security.W012", "security.W016"]
# Card printing uses a same-origin iframe, so framing is SAMEORIGIN rather than DENY (still no other sites).
SILENCED_SYSTEM_CHECKS = [*globals().get("SILENCED_SYSTEM_CHECKS", []), "security.W019"]

# ── Dispodex behaviour ────────────────────────────────────────────────────
PINKSHEET = {
    "BUSINESS_NAME": env("PINKSHEET_BUSINESS_NAME", "My Shop"),
    "REQUIRE_LOGIN": env_bool("PINKSHEET_REQUIRE_LOGIN", False),
    "BEHIND_HTTPS_PROXY": env_bool("PINKSHEET_BEHIND_HTTPS_PROXY", False),
    "MAINTENANCE_MODE": env_bool("PINKSHEET_MAINTENANCE_MODE", False),
    # A public showcase copy filled by `seed_demo`: made-up items, uploads and operator actions off.
    "DEMO_MODE": env_bool("PINKSHEET_DEMO_MODE", False),
    # The listing-images composer is unfinished: hidden (and its pages turned off) until it's ready.
    "LISTING_IMAGES": env_bool("PINKSHEET_LISTING_IMAGES", False),
    # Shared secret the imaging app sends with its JSON reports (blank = reports turned off).
    "IMAGING_API_KEY": env("PINKSHEET_IMAGING_API_KEY", ""),
    "MAINTENANCE_MESSAGE": env(
        "PINKSHEET_MAINTENANCE_MESSAGE",
        "Dispodex is temporarily offline for maintenance.",
    ),
    "HOST": env("PINKSHEET_HOST", "0.0.0.0"),
    "PORT": env_int("PINKSHEET_PORT", 8765),
    "THREADS": env_int("PINKSHEET_THREADS", 8),
    "WORKER_ENABLED": env_bool("PINKSHEET_WORKER_ENABLED", True),
    # Photos
    "PHOTO_MAX_BYTES": env_int("PINKSHEET_PHOTO_MAX_MB", 32) * 1024 * 1024,
    "PHOTO_MAX_DIMENSION": env_int("PINKSHEET_PHOTO_MAX_DIMENSION", 3000),
    # eBay rejects photos narrower than 500px; narrower SKU photos are enlarged to this width. 0 = off.
    "PHOTO_MIN_WIDTH": env_int("PINKSHEET_PHOTO_MIN_WIDTH", 500),
    "PHOTO_CONVERT_TO_PNG": env_bool("PINKSHEET_PHOTO_CONVERT_TO_PNG", True),
    # Backups
    "BACKUP_MIRROR_DIR": env("BACKUP_MIRROR_DIR", ""),
    "BACKUP_PHOTOS_MIRROR": env("BACKUP_PHOTOS_MIRROR", ""),
    "BACKUP_KEEP": env_int("BACKUP_KEEP", 0),
    "BACKUP_HOUR": env_int("BACKUP_HOUR", 2),
    "BACKUP_STALE_HOURS": env_int("BACKUP_STALE_HOURS", 36),
    "RECONCILE_HOUR": env_int("RECONCILE_HOUR", 3),
    # QZ Tray label printing
    "QZ_SIGNING_DIR": Path(env("QZ_SIGNING_DIR", str(DATA_DIR / "qz-signing"))),
    "QZ_ALLOWED_ORIGINS": env_list("QZ_ALLOWED_ORIGINS", []),
    # Text placed above every final eBay description (the shop's own policies; kept out of the code).
    "EBAY_BOILERPLATE_FILE": Path(env("EBAY_BOILERPLATE_FILE", str(DATA_DIR / "ebay_boilerplate.txt"))),
    # The shop's logo for the printed eBay sheet (SVG, PNG or JPG). Missing = no logo on the sheet.
    "PRINT_LOGO_FILE": Path(env("PINKSHEET_PRINT_LOGO_FILE", str(DATA_DIR / "print_logo.svg"))),
    # eBay taxonomy refresh (optional)
    "EBAY_CLIENT_ID": env("EBAY_CLIENT_ID", ""),
    "EBAY_CLIENT_SECRET": env("EBAY_CLIENT_SECRET", ""),
    "EBAY_MARKETPLACE_ID": env("EBAY_MARKETPLACE_ID", "EBAY_US"),
}

# ── Square (all optional — the app works without them) ─────────────────────
SQUARE = {
    "ENVIRONMENT": env("SQUARE_ENVIRONMENT", "sandbox").lower(),
    "SYNC_ENABLED": env_bool("SQUARE_SYNC_ENABLED", True),
    "ACCESS_TOKEN": env("SQUARE_ACCESS_TOKEN", ""),
    "LOCATION_ID": env("SQUARE_LOCATION_ID", ""),
    "API_VERSION": env("SQUARE_API_VERSION", "2026-07-15"),
    "CURRENCY": env("SQUARE_CURRENCY", "USD").upper(),
    "DEFAULT_QUANTITY": env_int("SQUARE_DEFAULT_QUANTITY", 1),
    "MAX_RETRIES": max(0, min(5, env_int("SQUARE_API_MAX_RETRIES", 3))),
    "TIMEOUT_SECONDS": max(5, min(120, env_int("SQUARE_API_TIMEOUT_SECONDS", 20))),
    "CONNECT_TIMEOUT_SECONDS": max(1, min(30, env_int("SQUARE_API_CONNECT_TIMEOUT_SECONDS", 5))),
    "WEBHOOK_SIGNATURE_KEY": env("SQUARE_WEBHOOK_SIGNATURE_KEY", ""),
    "WEBHOOK_NOTIFICATION_URL": env("SQUARE_WEBHOOK_NOTIFICATION_URL", ""),
    "WEBHOOK_MAX_AGE_SECONDS": max(300, env_int("SQUARE_WEBHOOK_MAX_AGE_SECONDS", 259200)),
    "WEBHOOK_MAX_BODY_BYTES": env_int("SQUARE_WEBHOOK_MAX_BODY_BYTES", 1048576),
}

# ── Logging ────────────────────────────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"},
        "raw": {"format": "{message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
        "app_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "app.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "plain",
            "encoding": "utf-8",
            "delay": True,
        },
        "square_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "square_sync.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "raw",
            "encoding": "utf-8",
            "delay": True,
        },
        "lookup_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "lookup.log"),
            "maxBytes": 2 * 1024 * 1024,
            "backupCount": 3,
            "formatter": "raw",
            "encoding": "utf-8",
            "delay": True,
        },
    },
    "loggers": {
        "django": {"handlers": ["console", "app_file"], "level": "WARNING", "propagate": False},
        "pinksheet": {"handlers": ["console", "app_file"], "level": "INFO", "propagate": False},
        "pinksheet.square": {"handlers": ["square_file", "app_file"], "level": "INFO", "propagate": False},
        "pinksheet.lookup": {"handlers": ["lookup_file"], "level": "INFO", "propagate": False},
    },
}
