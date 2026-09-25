from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from operations import backups, excel_import


class Command(BaseCommand):
    help = (
        "Import items and photos from the old app's 'Excel with photos' export (.xlsx). "
        "Shows a preview first; --replace swaps out the current items (the Archive is kept) "
        "after an automatic backup, then checks every item and photo against the file."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", help="The .xlsx file exported from the old app.")
        parser.add_argument("--dry-run", action="store_true", help="Only show what would be imported.")
        parser.add_argument("--replace", action="store_true", help="Remove the current items, photos and drafts first.")
        parser.add_argument("--yes", action="store_true", help="Don't ask for confirmation.")

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")
        try:
            report, _, _ = excel_import.plan(path)
        except excel_import.ExcelImportError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.MIGRATE_HEADING(f"Preview of {path.name}"))
        for line in report.lines():
            self.stdout.write(f"  {line}")
        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Preview only: nothing was changed."))
            return

        if options["replace"] and not options["yes"]:
            answer = input("\nThis replaces every current item, photo and draft (the Archive is kept). "
                           "A backup is made first. Type REPLACE to continue: ")
            if answer.strip().upper() != "REPLACE":
                raise CommandError("Cancelled. Nothing was changed.")

        result = backups.run_backup()
        if not result.ok:
            raise CommandError(f"The safety backup failed ({result.error}), so nothing was imported.")
        self.stdout.write("Backed up first: " + "; ".join(result.messages))

        try:
            excel_import.run(path, replace=options["replace"])
        except excel_import.ExcelImportError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Imported {report.items} items and {report.photos} photos."))

        self.stdout.write("Checking every item and photo against the file…")
        problems = excel_import.verify(path)
        if problems:
            for line in problems[:100]:
                self.stdout.write(self.style.ERROR(f"  {line}"))
            raise CommandError(f"{len(problems)} difference(s) found. Restore the backup above with "
                               "'manage.py restore_backup --latest' if needed.")
        self.stdout.write(self.style.SUCCESS("Verified: every item and every photo matches the file."))
