"""
Management command: build_fuel_cache

Geocodes all unique (city, state) pairs from the fuel CSV using ArcGIS
(no API key required) and saves results to FUEL_GEOCODE_CACHE_PATH.

Run this once before starting the server:

    python manage.py build_fuel_cache

The command is safe to re-run: it skips pairs already in the cache, so
you can resume an interrupted run or fill gaps from a partial cache.

Expected runtime: a few minutes for ~500 unique city/state pairs.
"""

from django.core.management.base import BaseCommand
from django.conf import settings

from routing.services.fuel_data import geocode_missing


class Command(BaseCommand):
    help = "Geocode fuel stop city/state pairs and save results to the cache file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-geocode all pairs, even those already in the cache.",
        )

    def handle(self, *args, **options):
        cache_path = settings.FUEL_GEOCODE_CACHE_PATH
        csv_path = settings.FUEL_CSV_PATH

        if options["force"] and cache_path.exists():
            cache_path.unlink()
            self.stdout.write("Cleared existing cache.")

        self.stdout.write(f"Cache path: {cache_path}")
        self.stdout.write(f"CSV path:   {csv_path}")
        self.stdout.write("Starting geocoding (ArcGIS, no rate limit)...\n")

        def progress(done: int, total: int, city: str, state: str) -> None:
            pct = int(done / total * 100)
            self.stdout.write(f"  [{pct:3d}%] {done}/{total}  {city}, {state}", ending="\r")
            self.stdout.flush()

        newly_done, already_cached = geocode_missing(cache_path, csv_path, progress)

        self.stdout.write("")  # newline after \r progress
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {newly_done} newly geocoded, "
                f"{already_cached} already cached.\n"
                f"Cache saved to {cache_path}"
            )
        )
