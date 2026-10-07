import csv
import json
import logging
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.fuel.models import FuelStation

logger = logging.getLogger(__name__)

CANADIAN_PROVINCE_CODES = {
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"
}

CONTIGUOUS_US_STATES = {
    "AL", "AR", "AZ", "CA", "CO", "CT", "DE", "FL", "GA", "IA", "ID", "IL",
    "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MS", "MT",
    "NC", "ND", "NE", "NH", "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY",
}


def normalize_city_name(city: str) -> str:
    """Normalize city name by stripping non-alphanumeric characters and lowercasing."""
    return re.sub(r"[^a-z0-9]", "", city.strip().lower())


class Command(BaseCommand):
    help = (
        "Ingests fuel stations from the OPIS CSV dataset with data cleaning, "
        "lowest-price deduplication, Canadian filtering, and gazetteer coordinate enrichment."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv-path",
            type=str,
            default=None,
            help="Path to the fuel prices CSV file. Defaults to workspace CSV if omitted.",
        )
        parser.add_argument(
            "--gazetteer-path",
            type=str,
            default=None,
            help="Path to the bundled US city gazetteer JSON file.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Batch size for database upsert operations.",
        )

    def _resolve_paths(self, csv_arg: str | None, gazetteer_arg: str | None) -> tuple[Path, Path]:
        """Resolve CSV and gazetteer file paths with flexible fallback."""
        if csv_arg:
            csv_path = Path(csv_arg)
        else:
            # Check current workspace parent, then BASE_DIR
            potential_csvs = [
                Path(settings.BASE_DIR).parent / "fuel-prices-for-be-assessment.csv",
                Path(settings.BASE_DIR) / "fuel-prices-for-be-assessment.csv",
                Path("fuel-prices-for-be-assessment.csv"),
            ]
            csv_path = next((p for p in potential_csvs if p.exists()), potential_csvs[0])

        if not csv_path.exists():
            raise CommandError(f"Fuel prices CSV not found at '{csv_path}'.")

        if gazetteer_arg:
            gazetteer_path = Path(gazetteer_arg)
        else:
            gazetteer_path = Path(__file__).resolve().parent.parent.parent / "data" / "us_cities_gazetteer.json"

        if not gazetteer_path.exists():
            raise CommandError(f"Gazetteer file not found at '{gazetteer_path}'.")

        return csv_path, gazetteer_path

    def _load_gazetteer(self, path: Path) -> dict[str, list[float]]:
        """Load bundled JSON gazetteer mapping normalized 'city_state' -> [lat, lon]."""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def handle(self, *args, **options):
        start_time = time.perf_counter()
        csv_path, gazetteer_path = self._resolve_paths(options["csv_path"], options["gazetteer_path"])
        batch_size = options["batch_size"]

        self.stdout.write(self.style.NOTICE(f"Loading gazetteer from: {gazetteer_path}"))
        gazetteer = self._load_gazetteer(gazetteer_path)
        self.stdout.write(f"Gazetteer loaded: {len(gazetteer):,} city entries.")

        self.stdout.write(self.style.NOTICE(f"Processing fuel prices CSV from: {csv_path}"))

        total_rows = 0
        canadian_skipped = 0
        non_contiguous_skipped = 0
        invalid_records = 0
        duplicates_collapsed = 0
        unmatched_coords = 0

        # In-memory deduplication map: station_id -> dict
        # Deduplication Rule: If multiple records exist for the same OPIS Truckstop ID,
        # retain the record with the LOWEST retail price (most cost-effective available rate).
        unique_stations: dict[int, dict] = {}

        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            required_cols = {"OPIS Truckstop ID", "Truckstop Name", "Address", "City", "State", "Retail Price"}
            if not required_cols.issubset(set(reader.fieldnames or [])):
                raise CommandError(f"CSV missing required columns. Found: {reader.fieldnames}")

            for row in reader:
                total_rows += 1

                # 1. Whitespace stripping across all fields
                raw_id = row.get("OPIS Truckstop ID", "").strip()
                raw_name = row.get("Truckstop Name", "").strip()
                raw_address = row.get("Address", "").strip()
                raw_city = row.get("City", "").strip()
                raw_state = row.get("State", "").strip().upper()
                raw_rack = row.get("Rack ID", "").strip()
                raw_price = row.get("Retail Price", "").strip()

                # 2. Validation & type parsing
                if not raw_id or not raw_name or not raw_price or not raw_state:
                    invalid_records += 1
                    logger.warning("Skipping invalid record with empty essential fields: %s", row)
                    continue

                try:
                    station_id = int(raw_id)
                except ValueError:
                    invalid_records += 1
                    logger.warning("Skipping record with non-integer station ID: '%s'", raw_id)
                    continue

                try:
                    price_decimal = Decimal(raw_price)
                    if price_decimal <= Decimal("0"):
                        invalid_records += 1
                        logger.warning("Skipping record with non-positive price: '%s'", raw_price)
                        continue
                except InvalidOperation:
                    invalid_records += 1
                    logger.warning("Skipping record with unparseable price: '%s'", raw_price)
                    continue

                rack_id = int(raw_rack) if raw_rack.isdigit() else None

                # 3. Geographic filtering
                if raw_state in CANADIAN_PROVINCE_CODES:
                    canadian_skipped += 1
                    continue

                if raw_state not in CONTIGUOUS_US_STATES:
                    non_contiguous_skipped += 1
                    logger.info("Skipping non-contiguous US record in state '%s'", raw_state)
                    continue

                station_candidate = {
                    "station_id": station_id,
                    "name": raw_name,
                    "address": raw_address,
                    "city": raw_city,
                    "state": raw_state,
                    "rack_id": rack_id,
                    "price_per_gallon": price_decimal,
                }

                # 4. Lowest-price deduplication policy
                if station_id in unique_stations:
                    duplicates_collapsed += 1
                    existing = unique_stations[station_id]
                    if price_decimal < existing["price_per_gallon"]:
                        unique_stations[station_id] = station_candidate
                else:
                    unique_stations[station_id] = station_candidate

        # 5. Coordinate enrichment via local gazetteer
        station_objects: list[FuelStation] = []
        enriched_count = 0

        for station_dict in unique_stations.values():
            norm_city = normalize_city_name(station_dict["city"])
            key = f"{norm_city}_{station_dict['state']}"

            coords = gazetteer.get(key)
            if coords:
                lat, lon = coords
                coord_source = "city_centroid"
                enriched_count += 1
            else:
                lat, lon = None, None
                coord_source = "unresolved"
                unmatched_coords += 1
                logger.warning(
                    "Unmatched coordinates for station %s in '%s, %s'",
                    station_dict["station_id"],
                    station_dict["city"],
                    station_dict["state"],
                )

            station_objects.append(
                FuelStation(
                    station_id=station_dict["station_id"],
                    name=station_dict["name"],
                    address=station_dict["address"],
                    city=station_dict["city"],
                    state=station_dict["state"],
                    rack_id=station_dict["rack_id"],
                    price_per_gallon=station_dict["price_per_gallon"],
                    latitude=lat,
                    longitude=lon,
                    coordinate_source=coord_source,
                )
            )

        # 6. Idempotent bulk upsert into database
        self.stdout.write(f"Persisting {len(station_objects):,} stations into database...")
        with transaction.atomic():
            FuelStation.objects.bulk_create(
                station_objects,
                batch_size=batch_size,
                update_conflicts=True,
                update_fields=[
                    "name",
                    "address",
                    "city",
                    "state",
                    "rack_id",
                    "price_per_gallon",
                    "latitude",
                    "longitude",
                    "coordinate_source",
                    "updated_at",
                ],
                unique_fields=["station_id"],
            )

        elapsed = time.perf_counter() - start_time

        # 7. Comprehensive summary report
        self.stdout.write(self.style.SUCCESS("\n" + "=" * 54))
        self.stdout.write(self.style.SUCCESS("           Fuel Station Import Summary"))
        self.stdout.write(self.style.SUCCESS("=" * 54))
        self.stdout.write(f"Total CSV rows processed:      {total_rows:>8,}")
        self.stdout.write(f"Loaded / Upserted stations:    {len(station_objects):>8,}")
        self.stdout.write(f"Duplicates collapsed:          {duplicates_collapsed:>8,}")
        self.stdout.write(f"Canadian records skipped:      {canadian_skipped:>8,}")
        self.stdout.write(f"Non-contiguous states skipped: {non_contiguous_skipped:>8,}")
        self.stdout.write(f"Invalid records skipped:       {invalid_records:>8,}")
        self.stdout.write(f"Coordinates enriched:          {enriched_count:>8,} (city_centroid)")
        self.stdout.write(f"Unmatched coordinates:         {unmatched_coords:>8,}")
        self.stdout.write(f"Execution time:                {elapsed:>7.2f}s")
        self.stdout.write(self.style.SUCCESS("=" * 54 + "\n"))
