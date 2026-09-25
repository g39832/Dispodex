import time

from django.core.management.base import BaseCommand

from operations.worker import Worker


class Command(BaseCommand):
    help = "Run only the background worker (Square queue, nightly backup, daily reconciliation)."

    def handle(self, *args, **options):
        worker = Worker()
        worker.start()
        self.stdout.write(self.style.SUCCESS("Worker running. Press Ctrl+C to stop."))
        try:
            while worker.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            worker.stop()
            worker.join(timeout=10)
