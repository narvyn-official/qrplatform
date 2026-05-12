from django.core.management.base import BaseCommand

from apps.analytics.tasks import process_pending_scan_events_now


class Command(BaseCommand):
    help = "Process captured QR scan events that have not been counted yet."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--geolocate", action="store_true")

    def handle(self, *args, **options):
        result = process_pending_scan_events_now(
            limit=options["limit"],
            geolocate=options["geolocate"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Processed {processed} pending scans, {failed} failed, {remaining} remaining.".format(**result)
            )
        )
