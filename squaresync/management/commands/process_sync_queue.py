from django.core.management.base import BaseCommand

from squaresync import processor


class Command(BaseCommand):
    help = "Push waiting Square jobs now (the background worker normally does this for you)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        summary = processor.process_queue(limit=options["limit"])
        if summary["waiting_for_config"]:
            self.stdout.write(self.style.WARNING("Square isn't configured yet; jobs stay queued until it is."))
            return
        self.stdout.write(f"Processed {summary['processed']}: {summary['ok']} ok, {summary['failed']} failed.")
