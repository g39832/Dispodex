"""Test settings: everything runs in a throw-away data folder."""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="pinksheet-tests-")
os.environ["PINKSHEET_DATA_DIR"] = _TMP
os.environ["PINKSHEET_SKIP_DOTENV"] = "1"
os.environ.setdefault("DJANGO_SECRET_KEY", "tests-only-secret-key")
for _name in list(os.environ):
    if _name.startswith("SQUARE_") or _name.startswith("BACKUP_"):
        del os.environ[_name]

from pinksheet.settings import *  # noqa: E402,F401,F403

DATABASES["default"]["TEST"] = {"NAME": os.path.join(_TMP, "test.sqlite3")}  # noqa: F405
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES["staticfiles"]["BACKEND"] = "django.contrib.staticfiles.storage.StaticFilesStorage"  # noqa: F405
PINKSHEET["WORKER_ENABLED"] = False  # noqa: F405
LOGGING["loggers"]["pinksheet"]["handlers"] = ["app_file"]  # noqa: F405
LOGGING["loggers"]["django"]["handlers"] = ["app_file"]  # noqa: F405
os.makedirs(STATIC_ROOT, exist_ok=True)  # noqa: F405
