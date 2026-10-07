import csv
import json
from decimal import Decimal
from io import StringIO
from pathlib import Path
import pytest
from django.core.management import call_command

from apps.fuel.models import FuelStation
from apps.fuel.services.spatial_service import (
    FuelStationSpatialService,
    haversine_miles,
)


@pytest.fixture
def sample_gazetteer_file(tmp_path):
    """Temporary gazetteer file containing sample normalized city coordinates."""
    gazetteer_data = {
        "newyork_NY": [40.7128, -74.0060],
        "boston_MA": [42.3601, -71.0589],
        "newhaven_CT": [41.3083, -72.9279],
        "hartford_CT": [41.7658, -72.6734],
        "springfield_MA": [42.1015, -72.5898],
        "stamford_CT": [41.0534, -73.5387],
        "providence_RI": [41.8240, -71.4128],
    }
    file_path = tmp_path / "test_gazetteer.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(gazetteer_data, f)
    return file_path


@pytest.fixture
def sample_csv_file(tmp_path):
    """Temporary fuel CSV file with edge cases, duplicates, invalid rows, and Canadian rows."""
    content = (
        "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"
        # Valid US station 1
        "1001,NYC TRAVEL PLAZA,I-95 EXIT 1,New York  ,NY,501,3.459\n"
        # Duplicate station 1001 with LOWER price (should override 3.459)
        "1001,NYC TRAVEL PLAZA,I-95 EXIT 1,New York,NY,501,3.199\n"
        # Duplicate station 1001 with HIGHER price (should NOT override 3.199)
        "1001,NYC TRAVEL PLAZA,I-95 EXIT 1,New York,NY,501,3.699\n"
        # Valid US station 2 (New Haven, CT)
        "1002,NEW HAVEN STOP,I-95 EXIT 46,  New Haven  ,CT,502,3.299\n"
        # Valid US station 3 (Hartford, CT)
        "1003,HARTFORD TRAVEL,I-91 EXIT 32,Hartford,CT,503,3.399\n"
        # Canadian station (should be filtered out)
        "2001,TORONTO FUEL,HWY 401,Toronto,ON,901,4.199\n"
        "2002,MONTREAL STOP,TCH-20,Montreal,QC,902,4.299\n"
        # Non-contiguous state (should be filtered out)
        "3001,ALASKA FUEL,PARKS HWY,Anchorage,AK,701,4.599\n"
        # Invalid row: non-numeric price
        "4001,BROKEN PRICE,I-80,Denver,CO,601,INVALID_PRICE\n"
        # Invalid row: non-integer ID
        "NOT_AN_ID,BAD ID,I-80,Denver,CO,601,3.599\n"
        # Invalid row: negative price
        "4002,NEGATIVE PRICE,I-80,Denver,CO,601,-2.50\n"
        # Unmatched city (should be loaded with null coordinates and 'unresolved' source)
        "5001,MYSTERY STATION,RURAL ROUTE 9,Unknownville,NE,801,3.159\n"
    )
    file_path = tmp_path / "test_fuel_prices.csv"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return file_path


@pytest.mark.django_db
class TestFuelDataIngestion:
    """Tests A through J: Ingestion, cleaning, deduplication, and coordinate enrichment."""

    def test_ingestion_pipeline_end_to_end(self, sample_csv_file, sample_gazetteer_file):
        """Covers A (CSV loading), B (whitespace), C (price), D (Canadian filtering),

        E & F (lowest-price deduplication), G (invalid records), H (coordinate matching),
        I (unmatched city handling), and J (idempotent imports).
        """
        # --- First Import Execution ---
        out = StringIO()
        call_command(
            "load_fuel_stations",
            csv_path=str(sample_csv_file),
            gazetteer_path=str(sample_gazetteer_file),
            stdout=out,
        )
        output_text = out.getvalue()

        # Check summary prints
        assert "Total CSV rows processed:            12" in output_text
        assert "Canadian records skipped:             2" in output_text
        assert "Non-contiguous states skipped:        1" in output_text
        assert "Invalid records skipped:              3" in output_text
        assert "Duplicates collapsed:                 2" in output_text

        # Verify database contents
        # Total valid unique US stations: 1001, 1002, 1003, 5001 = 4 stations
        assert FuelStation.objects.count() == 4

        # A & B: Whitespace normalization
        s1001 = FuelStation.objects.get(station_id=1001)
        assert s1001.city == "New York"
        assert s1001.state == "NY"

        s1002 = FuelStation.objects.get(station_id=1002)
        assert s1002.city == "New Haven"
        assert s1002.state == "CT"

        # C, E, & F: Price parsing & lowest-price deduplication
        # Out of [3.459, 3.199, 3.699], lowest is 3.199
        assert s1001.price_per_gallon == Decimal("3.199")

        # D: Canadian stations (2001, 2002) are not in DB
        assert not FuelStation.objects.filter(station_id__in=[2001, 2002]).exists()

        # Non-contiguous (3001) not in DB
        assert not FuelStation.objects.filter(station_id=3001).exists()

        # G: Invalid records (4001, 4002) not in DB
        assert not FuelStation.objects.filter(station_id__in=[4001, 4002]).exists()

        # H: Coordinate matching
        assert s1001.latitude == 40.7128
        assert s1001.longitude == -74.0060
        assert s1001.coordinate_source == "city_centroid"

        # I: Unmatched city handling
        s5001 = FuelStation.objects.get(station_id=5001)
        assert s5001.latitude is None
        assert s5001.longitude is None
        assert s5001.coordinate_source == "unresolved"

        # J: Idempotent imports - Run a second time
        out2 = StringIO()
        call_command(
            "load_fuel_stations",
            csv_path=str(sample_csv_file),
            gazetteer_path=str(sample_gazetteer_file),
            stdout=out2,
        )
        assert FuelStation.objects.count() == 4  # Count must NOT change!


class TestSpatialService:
    """Tests K through O: Spatial indexing, corridor filtering, and projection."""

    @pytest.fixture
    def spatial_service(self):
        service = FuelStationSpatialService()
        service.invalidate_index()
        return service

    @pytest.fixture
    def synthetic_stations(self):
        """Synthetic stations:

        - Station 101: On route corridor near NYC (40.7200, -74.0000) ~ 0.6 mi from route
        - Station 102: On route corridor near New Haven (41.3100, -72.9200) ~ 0.5 mi from route
        - Station 103: On route corridor near Providence (41.8250, -71.4100) ~ 0.2 mi from route
        - Station 104: Far off route (42.8000, -73.0000) ~ 100+ miles away (Albany area)
        """
        return [
            {
                "station_id": 101,
                "name": "STATION NEAR NYC",
                "address": "I-95",
                "city": "New York",
                "state": "NY",
                "price_per_gallon": Decimal("3.150"),
                "latitude": 40.7200,
                "longitude": -74.0000,
            },
            {
                "station_id": 102,
                "name": "STATION NEAR NEW HAVEN",
                "address": "I-95",
                "city": "New Haven",
                "state": "CT",
                "price_per_gallon": Decimal("3.250"),
                "latitude": 41.3100,
                "longitude": -72.9200,
            },
            {
                "station_id": 103,
                "name": "STATION NEAR PROVIDENCE",
                "address": "I-95",
                "city": "Providence",
                "state": "RI",
                "price_per_gallon": Decimal("3.350"),
                "latitude": 41.8250,
                "longitude": -71.4100,
            },
            {
                "station_id": 104,
                "name": "STATION OFF ROUTE",
                "address": "I-87",
                "city": "Albany",
                "state": "NY",
                "price_per_gallon": Decimal("2.990"),
                "latitude": 42.8000,
                "longitude": -73.0000,
            },
        ]

    @pytest.fixture
    def nyc_to_boston_line(self):
        """Simplified route geometry from NYC to Boston."""
        return {
            "type": "LineString",
            "coordinates": [
                [-74.0060, 40.7128],  # NYC
                [-73.5000, 41.2000],  # Stamford
                [-72.9279, 41.3083],  # New Haven
                [-72.0995, 41.3557],  # New London
                [-71.4128, 41.8240],  # Providence
                [-71.0589, 42.3601],  # Boston
            ],
        }

    def test_spatial_index_construction(self, spatial_service, synthetic_stations):
        """K. Spatial index construction indexes all stations with valid coordinates."""
        count = spatial_service.build_index(synthetic_stations)
        assert count == 4
        assert spatial_service.is_indexed() is True

    def test_route_corridor_filtering(
        self, spatial_service, synthetic_stations, nyc_to_boston_line
    ):
        """L. Stations within 5-mile corridor are included; stations > 5 miles away are excluded."""
        spatial_service.build_index(synthetic_stations)

        candidates = spatial_service.find_candidates_near_route(
            nyc_to_boston_line, corridor_miles=5.0
        )
        candidate_ids = [c.station_id for c in candidates]

        # Stations 101, 102, 103 are within 5 miles
        assert 101 in candidate_ids
        assert 102 in candidate_ids
        assert 103 in candidate_ids

        # Station 104 is ~100 miles away and MUST be excluded
        assert 104 not in candidate_ids
        assert len(candidates) == 3

    def test_distance_calculations(
        self, spatial_service, synthetic_stations, nyc_to_boston_line
    ):
        """M & N. Verify distance_from_route_miles and distance_along_route_miles."""
        spatial_service.build_index(synthetic_stations)

        candidates = spatial_service.find_candidates_near_route(
            nyc_to_boston_line, corridor_miles=5.0
        )

        # Candidates are ordered by distance_along_route_miles monotonically
        alongs = [c.distance_along_route_miles for c in candidates]
        assert alongs == sorted(alongs)

        # Station 101 is near the start (NYC)
        c101 = next(c for c in candidates if c.station_id == 101)
        assert c101.distance_from_route_miles < 2.0
        assert c101.distance_along_route_miles < 20.0

        # Station 102 is near New Haven (~70-80 miles into the trip)
        c102 = next(c for c in candidates if c.station_id == 102)
        assert c102.distance_from_route_miles < 2.0
        assert 60.0 < c102.distance_along_route_miles < 100.0

        # Station 103 is near Providence (~150-170 miles into the trip)
        c103 = next(c for c in candidates if c.station_id == 103)
        assert c103.distance_from_route_miles < 2.0
        assert 140.0 < c103.distance_along_route_miles < 185.0

    def test_duplicate_candidate_removal(self, spatial_service, nyc_to_boston_line):
        """O. Duplicate candidate stations are never returned in candidate list."""
        # Include duplicate identical stations in input
        stations = [
            {
                "station_id": 999,
                "name": "TEST STATION",
                "address": "I-95",
                "city": "New Haven",
                "state": "CT",
                "price_per_gallon": Decimal("3.00"),
                "latitude": 41.3100,
                "longitude": -72.9200,
            },
            {
                "station_id": 999,
                "name": "TEST STATION",
                "address": "I-95",
                "city": "New Haven",
                "state": "CT",
                "price_per_gallon": Decimal("3.00"),
                "latitude": 41.3100,
                "longitude": -72.9200,
            },
        ]
        spatial_service.build_index(stations)

        candidates = spatial_service.find_candidates_near_route(
            nyc_to_boston_line, corridor_miles=5.0
        )
        assert len(candidates) == 1
        assert candidates[0].station_id == 999

    def test_empty_or_trivial_route(self, spatial_service, synthetic_stations):
        """Edge case: Route geometry with < 2 points returns empty candidates list."""
        spatial_service.build_index(synthetic_stations)
        assert spatial_service.find_candidates_near_route({"coordinates": []}) == []
        assert spatial_service.find_candidates_near_route({"coordinates": [[-74.0, 40.7]]}) == []
