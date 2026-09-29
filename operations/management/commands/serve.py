"""Run Dispodex for real: the waitress web server plus the background worker, in one process."""
import logging
import os
import signal
import socket

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger("pinksheet")


def _stop_on_signal(signum, frame):
    raise KeyboardInterrupt  # waitress shuts down cleanly on this, then the worker stops


def _handle_stop_signals() -> None:
    """Stop cleanly on Ctrl+C and on `docker stop` / service managers (SIGTERM).

    Python only turns SIGINT into KeyboardInterrupt when it wasn't ignored at start-up
    (it is for background jobs), and never handles SIGTERM, which as PID 1 in a
    container would otherwise be ignored until Docker kills the process.
    """
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _stop_on_signal)


def _lan_addresses() -> list[str]:
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return sorted({info[4][0] for info in infos if not info[4][0].startswith("127.")})


def reachable_from_internet() -> bool:
    """True when .env says Dispodex sits behind an HTTPS tunnel/proxy or trusts an https:// origin."""
    return settings.PINKSHEET["BEHIND_HTTPS_PROXY"] or any(
        origin.lower().startswith("https://") for origin in settings.CSRF_TRUSTED_ORIGINS
    )


class Command(BaseCommand):
    help = "Start Dispodex (web server + background worker). This is what start.bat runs."

    def add_arguments(self, parser):
        parser.add_argument("--host", default=settings.PINKSHEET["HOST"])
        parser.add_argument("--port", type=int, default=settings.PINKSHEET["PORT"])
        parser.add_argument("--threads", type=int, default=settings.PINKSHEET["THREADS"])
        parser.add_argument("--no-worker", action="store_true", help="Don't start the background worker.")
        parser.add_argument("--skip-setup", action="store_true", help="Skip migrate/collectstatic on start.")

    def _backup_before_update(self) -> None:
        """An update that changes the database is backed up first, so it can be undone."""
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        from operations import backups

        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if not pending or not settings.DATABASES["default"]["NAME"].exists():
            return
        if not connection.introspection.table_names():
            return  # brand-new install: nothing to protect yet
        result = backups.run_backup()
        if not result.ok:
            raise CommandError(f"Database update found, but the safety backup failed: {result.error}. Nothing was changed.")
        self.stdout.write("Database update found: backed up first. " + "; ".join(result.messages))

    def handle(self, *args, **options):
        from waitress import serve

        from operations.worker import Worker

        # Through a tunnel every visitor looks like localhost, so the private-network
        # checks stop protecting anything: the only thing left is the sign-in wall.
        if reachable_from_internet() and not settings.PINKSHEET["REQUIRE_LOGIN"] and not settings.PINKSHEET["DEMO_MODE"]:
            raise CommandError(
                "Dispodex is set up to be reached over HTTPS, but sign-in is off, so anyone with the address "
                "could see and export the whole inventory. Set PINKSHEET_REQUIRE_LOGIN=1 in .env "
                "(create accounts with: python manage.py createsuperuser), then start again."
            )

        if not options["skip_setup"]:
            self._backup_before_update()
            call_command("migrate", interactive=False, verbosity=0)
            if not settings.DEBUG:
                call_command("collectstatic", interactive=False, verbosity=0)

        # Build the web app only now: the static-file server indexes the files
        # when it is created, so it must come after collectstatic has run.
        from pinksheet.wsgi import application

        worker = None
        if settings.PINKSHEET["WORKER_ENABLED"] and not options["no_worker"]:
            worker = Worker()
            worker.start()

        port = options["port"]
        _handle_stop_signals()
        self.stdout.write(self.style.SUCCESS("Dispodex is running."))
        if os.environ.get("PINKSHEET_IN_DOCKER"):
            # The container's own addresses are useless to visitors: point at the server instead.
            self.stdout.write("  In Docker: open http://<this server's address>:<published port>/ (8765 unless changed).")
        else:
            self.stdout.write(f"  On this computer:   http://localhost:{port}/")
            for address in _lan_addresses():
                self.stdout.write(f"  On the shop network: http://{address}:{port}/")
            self.stdout.write("  Press Ctrl+C to stop.")
        try:
            serve(
                application,
                host=options["host"],
                port=port,
                threads=options["threads"],
                max_request_body_size=settings.MAX_REQUEST_BODY_SIZE,
                channel_timeout=300,
                ident="Dispodex",
            )
        finally:
            if worker:
                worker.stop()
        # Waitress returns quietly on Ctrl+C (or when the window is told to close), so say so.
        logger.info("Web server stopped.")
        if os.environ.get("PINKSHEET_IN_DOCKER"):
            self.stdout.write("Dispodex has stopped.")
            return
        self.stdout.write(self.style.WARNING(
            "Dispodex has stopped (Ctrl+C was pressed in this window, or it was closed). "
            "Run start.bat to start it again."
        ))
