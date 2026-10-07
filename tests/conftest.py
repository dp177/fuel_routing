import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.routing.services.contracts import GeoPoint


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear Django cache before every test to ensure test isolation."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def nyc_point():
    return GeoPoint(
        latitude=40.7128,
        longitude=-74.0060,
        display_name="New York, NY, USA",
        country_code="us",
        state="New York",
    )


@pytest.fixture
def boston_point():
    return GeoPoint(
        latitude=42.3601,
        longitude=-71.0589,
        display_name="Boston, MA, USA",
        country_code="us",
        state="Massachusetts",
    )


@pytest.fixture
def mock_nominatim_nyc():
    return [
        {
            "place_id": 1001,
            "lat": "40.7128",
            "lon": "-74.0060",
            "display_name": "New York, NY, USA",
            "address": {
                "city": "New York",
                "state": "New York",
                "country_code": "us",
                "country": "United States",
            },
        }
    ]


@pytest.fixture
def mock_nominatim_boston():
    return [
        {
            "place_id": 1002,
            "lat": "42.3601",
            "lon": "-71.0589",
            "display_name": "Boston, MA, USA",
            "address": {
                "city": "Boston",
                "state": "Massachusetts",
                "country_code": "us",
                "country": "United States",
            },
        }
    ]


@pytest.fixture
def mock_nominatim_toronto():
    return [
        {
            "place_id": 2001,
            "lat": "43.6532",
            "lon": "-79.3832",
            "display_name": "Toronto, Ontario, Canada",
            "address": {
                "city": "Toronto",
                "state": "Ontario",
                "country_code": "ca",
                "country": "Canada",
            },
        }
    ]


@pytest.fixture
def mock_osrm_nyc_to_boston():
    return {
        "code": "Ok",
        "routes": [
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [-74.0060, 40.7128],
                        [-73.5000, 41.2000],
                        [-72.0000, 41.8000],
                        [-71.0589, 42.3601],
                    ],
                },
                "legs": [
                    {
                        "summary": "I-95 N",
                        "weight": 14200.0,
                        "duration": 14400.0,
                        "distance": 346000.0,
                    }
                ],
                "weight_name": "routability",
                "weight": 14200.0,
                "duration": 14400.0,  # 4 hours
                "distance": 346000.0,  # ~215 miles
            }
        ],
        "waypoints": [
            {"name": "New York", "location": [-74.0060, 40.7128]},
            {"name": "Boston", "location": [-71.0589, 42.3601]},
        ],
    }
