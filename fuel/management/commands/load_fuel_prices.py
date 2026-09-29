import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from fuel.models import FuelStation

DEFAULT_CSV_PATH = Path(settings.BASE_DIR) / "fuel" / "data" / "fuel_prices.csv"
DEFAULT_GEOCODE_CACHE_PATH = Path(settings.BASE_DIR) / "fuel" / "data" / "geocode_cache.json"


class Command(BaseCommand):
    """Load the OPIS fuel-price CSV into FuelStation rows.

    Coordinates are looked up from a pre-baked City+State -> lat/lon cache
    (built offline via a one-time Nominatim geocoding pass, see
    fuel/data/geocode_cache.json) rather than geocoded here. This keeps the
    load fast and fully offline, and keeps the app's live external API
    calls limited to routing/geocoding a single trip request.
    """

    help = "Load fuel station prices from CSV, joined to a pre-baked geocode cache."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", nargs="?", default=str(DEFAULT_CSV_PATH))
        parser.add_argument(
            "--geocode-cache",
            dest="geocode_cache_path",
            default=str(DEFAULT_GEOCODE_CACHE_PATH),
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete existing FuelStation rows before loading.",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"])
        cache_path = Path(options["geocode_cache_path"])

        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")
        if not cache_path.exists():
            raise CommandError(
                f"Geocode cache not found: {cache_path}. "
                "Run the offline geocoding step first (see README)."
            )

        geocode_cache = json.loads(cache_path.read_text())

        if options["clear"]:
            deleted, _ = FuelStation.objects.all().delete()
            self.stdout.write(f"Cleared {deleted} existing FuelStation rows.")

        stations = []
        skipped_no_geocode = 0
        skipped_bad_row = 0

        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                city = row["City"].strip()
                state = row["State"].strip()
                key = f"{city}|{state}"
                coords = geocode_cache.get(key)

                try:
                    price = Decimal(row["Retail Price"].strip())
                    opis_id = int(row["OPIS Truckstop ID"].strip())
                    rack_id = int(row["Rack ID"].strip())
                except (InvalidOperation, ValueError, KeyError):
                    skipped_bad_row += 1
                    continue

                if coords is None:
                    skipped_no_geocode += 1

                stations.append(
                    FuelStation(
                        opis_id=opis_id,
                        name=row["Truckstop Name"].strip(),
                        address=row["Address"].strip(),
                        city=city,
                        state=state,
                        rack_id=rack_id,
                        price_per_gallon=price,
                        latitude=coords["lat"] if coords else None,
                        longitude=coords["lon"] if coords else None,
                    )
                )

        FuelStation.objects.bulk_create(stations, batch_size=1000)

        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {len(stations)} fuel stations "
                f"({skipped_no_geocode} without coordinates, "
                f"{skipped_bad_row} rows skipped for bad data)."
            )
        )
