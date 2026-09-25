from django.core.management.base import BaseCommand, CommandError

from operations import backups


class Command(BaseCommand):
    help = "Make a backup of the database now (and copy it to the mirror folder if one is set)."

    def add_arguments(self, parser):
        parser.add_argument("--verify", action="store_true", help="Also verify the newest backup afterwards.")

    def handle(self, *args, **options):
        result = backups.run_backup()
        for message in result.messages:
            self.stdout.write(f"  - {message}")
        if not result.ok:
            raise CommandError(result.error or "Backup failed.")
        if options["verify"]:
            outcome = backups.verify_latest()
            for message in outcome["messages"]:
                self.stdout.write(f"  - {message}")
            if not outcome["ok"]:
                raise CommandError("Verification failed.")
        self.stdout.write(self.style.SUCCESS("Backup complete."))
