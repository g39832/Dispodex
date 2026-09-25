from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from archive.importer import import_csv


class Command(BaseCommand):
    help = "Import a legacy CSV export into the Archive (safe to re-run; duplicates are skipped)."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")
        parser.add_argument("--source", default="", help="Where the data came from, e.g. 'Old Server'.")
        parser.add_argument("--table", default="", help="Original table name.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        path = Path(options["csv_path"])
        if not path.exists():
            raise CommandError(f"CSV not found: {path}")
        report = import_csv(path, source=options["source"], table=options["table"], dry_run=options["dry_run"])
        for warning in report.warnings[:50]:
            self.stderr.write(warning)
        verb = "Would import" if options["dry_run"] else "Imported"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {report.inserted} of {report.processed} rows ({report.skipped} skipped)."
        ))
