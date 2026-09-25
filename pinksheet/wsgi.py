"""WSGI entry point used by waitress (see `python manage.py serve`)."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pinksheet.settings")

application = get_wsgi_application()
