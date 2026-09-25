from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from operations.legacy_import import run_import


class Command(BaseCommand):
    help = "Copy items, photos, archive and Square data from the old PHP Pinksheet folder (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("source", help=r"The old app's folder, e.g. C:\path\to\old\pinksheet")
        parser.add_argument("--replace", action="store_true", help="Wipe this app's items first and import fresh.")

    def handle(self, *args, **options):
        try:
            summary = run_import(Path(options["source"]), replace=options["replace"])
        except (FileNotFoundError, RuntimeError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Import finished."))
        for line in summary.lines():
            self.stdout.write(f"  - {line}")
