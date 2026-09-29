import csv
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

DEFAULT_CSV_PATH = Path(settings.BASE_DIR) / "fuel" / "data" / "fuel_prices.csv"
DEFAULT_CACHE_PATH = Path(settings.BASE_DIR) / "fuel" / "data" / "geocode_cache.json"
USER_AGENT = "django-fuel-route-planner/1.0 (one-time offline geocode batch)"
NOMINATIM_RATE_LIMIT_SECONDS = 1.1  # Nominatim's usage policy caps this at 1 req/s.


class Command(BaseCommand):
    """One-time offline step: geocode every unique City+State in the fuel
    price CSV via Nominatim, and cache the result to a JSON file.

    This is NOT called at request time or at `load_fuel_prices` time — it's
    a build step you re-run only when the source CSV changes. Committing
    its output (fuel/data/geocode_cache.json) means `load_fuel_prices` runs
    instantly and fully offline for anyone cloning the repo.

    Safe to re-run: already-cached City+State pairs are skipped, so an
    interrupted run just picks up where it left off.
    """

    help = "Build/refresh the City+State -> lat/lon geocode cache used by load_fuel_prices."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", nargs="?", default=str(DEFAULT_CSV_PATH))
        parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"])
        cache_path = Path(options["cache_path"])

        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        with csv_path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        pairs = sorted({(row["City"].strip(), row["State"].strip()) for row in rows})

        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        remaining = [p for p in pairs if f"{p[0]}|{p[1]}" not in cache]

        self.stdout.write(f"{len(cache)} cached already, {len(remaining)} left to geocode.")

        for i, (city, state) in enumerate(remaining, start=1):
            key = f"{city}|{state}"
            query = urllib.parse.urlencode(
                {"q": f"{city}, {state}, USA", "format": "json", "limit": 1}
            )
            url = f"{settings.NOMINATIM_BASE_URL}/search?{query}"
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=10) as response:
                    data = json.loads(response.read())
                cache[key] = {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])} if data else None
            except Exception as exc:  # noqa: BLE001 - best-effort batch geocode, log and continue
                self.stderr.write(f"Failed to geocode {key}: {exc}")
                cache[key] = None

            if i % 25 == 0:
                cache_path.write_text(json.dumps(cache))
                self.stdout.write(f"progress: {len(cache)}/{len(pairs)}")

            time.sleep(NOMINATIM_RATE_LIMIT_SECONDS)

        cache_path.write_text(json.dumps(cache))
        self.stdout.write(self.style.SUCCESS(f"Done: {len(cache)}/{len(pairs)} City+State pairs cached."))
