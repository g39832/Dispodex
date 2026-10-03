import sqlite3
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from operations import backups, original_photos


class Command(BaseCommand):
    help = (
        "Replace the blurry low-res photos from the old app's Excel export with the old app's original "
        "photos (read from its data/sku_photos folder, never changed). Shows a preview first and makes "
        "a backup before changing anything."
    )

    def add_arguments(self, parser):
        parser.add_argument("source", help=r"The old app's folder (the one holding data/intake.sqlite and data/sku_photos)")
        parser.add_argument("--dry-run", action="store_true", help="Only show what would change.")
        parser.add_argument("--yes", action="store_true", help="Don't ask for confirmation.")
        parser.add_argument("--actor", default="System", help="Name shown in each item's History.")

    def handle(self, *args, **options):
        source = Path(options["source"])
        try:
            preview = original_photos.plan(source)
        except (FileNotFoundError, sqlite3.Error) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.MIGRATE_HEADING("Preview"))
        for line in preview.lines():
            self.stdout.write(f"  {line}")
        if options["dry_run"] or not preview.skus:
            self.stdout.write(self.style.SUCCESS("Nothing was changed."))
            return
        if not options["yes"]:
            answer = input("\nA backup is made first. Type YES to replace these photos: ")
            if answer.strip().upper() != "YES":
                raise CommandError("Cancelled. Nothing was changed.")

        result = backups.run_backup()
        if not result.ok:
            raise CommandError(f"The safety backup failed ({result.error}), so nothing was changed.")
        self.stdout.write("Backed up first: " + "; ".join(result.messages))

        done = original_photos.run(
            source, actor=options["actor"],
            progress=lambda n, total: self.stdout.write(f"  {n} of {total} items…"),
        )
        replaced = len(done.skus) - len(done.failed)
        self.stdout.write(self.style.SUCCESS(f"Gave {replaced} item(s) their original photos."))
        for line in done.lines()[2:]:
            self.stdout.write(f"  {line}")
