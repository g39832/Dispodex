from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from operations import backups


class Command(BaseCommand):
    help = "Replace the live database with a backup. STOP Dispodex first (close start.bat)."

    def add_arguments(self, parser):
        parser.add_argument("backup", nargs="?", help="Backup file name or path. Leave out with --latest.")
        parser.add_argument("--latest", action="store_true", help="Restore the newest backup.")
        parser.add_argument("--yes", action="store_true", help="Don't ask for confirmation.")

    def handle(self, *args, **options):
        if options["latest"]:
            target = backups.latest_backup()
            if target is None:
                raise CommandError("There are no backups yet.")
        elif options["backup"]:
            target = Path(options["backup"])
            if not target.exists():
                target = backups.backup_dir() / options["backup"]
            if not target.exists():
                raise CommandError(f"Backup not found: {options['backup']}")
        else:
            raise CommandError("Give a backup file name, or use --latest.")

        self.stdout.write(f"About to restore {target.name}. Dispodex must be stopped first.")
        if not options["yes"] and input("Type RESTORE to continue: ").strip() != "RESTORE":
            raise CommandError("Cancelled.")
        try:
            safety = backups.restore(target)
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Restored {target.name}."))
        self.stdout.write(f"The database as it was before restoring is saved at {safety}")
