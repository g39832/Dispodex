from django.core.management.base import BaseCommand, CommandError

from squaresync import reconciliation
from squaresync.models import ReconciliationRun


class Command(BaseCommand):
    help = "Compare Dispodex with Square, fix what is safe, and report the rest."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report problems without fixing anything.")
        parser.add_argument("--no-catalog", action="store_true", help="Skip downloading Square's catalog.")

    def handle(self, *args, **options):
        run = reconciliation.run(
            trigger=ReconciliationRun.Trigger.MANUAL, dry_run=options["dry_run"], fetch_catalog=not options["no_catalog"]
        )
        if run.status != ReconciliationRun.State.COMPLETED:
            raise CommandError(f"Run #{run.pk} failed: {run.error_message}")
        self.stdout.write(
            f"Run #{run.pk}: {run.issues_detected} found, {run.issues_repaired} fixed, "
            f"{run.manual_actions_required} need a person ({run.runtime_seconds}s)."
        )
        for issue in run.issues.all()[:50]:
            self.stdout.write(f"  [{issue.severity}] {issue.description} → {issue.get_repair_status_display()}")
