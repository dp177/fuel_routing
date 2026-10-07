from unittest.mock import patch
import pytest
from django.urls import reverse

from apps.core.exceptions import (
    LocationNotFoundError,
    RoutingNotFoundError,
    RoutingTimeoutError,
)
from apps.routing.services.contracts import GeoPoint, RouteGeometry, RouteResult
from apps.routing.services.geocoder import LocationService
from apps.routing.services.osrm import OsrmRoutingService
from apps.fuel.services.spatial_service import FuelStationSpatialService



@pytest.mark.django_db
class TestRouteFuelPlanAPI:
    """Test suite for POST /api/v1/route/fuel-plan/ endpoint."""

    url = reverse("fuel:route-fuel-plan")

    def test_missing_start_parameter(self, api_client):
        """9a. Missing 'start' returns 400 Bad Request with field error."""
        response = api_client.post(self.url, {"finish": "Boston, MA"}, format="json")
        assert response.status_code == 400
        data = response.json()
        assert data["status"] == "error"
        assert "start" in data["details"]

    def test_missing_finish_parameter(self, api_client):
        """9b. Missing 'finish' returns 400 Bad Request with field error."""
        response = api_client.post(self.url, {"start": "New York, NY"}, format="json")
        assert response.status_code == 400
        data = response.json()
        assert data["status"] == "error"
        assert "finish" in data["details"]

    def test_malformed_empty_payload(self, api_client):
        """9c. Blank or empty strings return 400 Bad Request."""
        response = api_client.post(self.url, {"start": "   ", "finish": ""}, format="json")
        assert response.status_code == 400
        data = response.json()
        assert data["status"] == "error"

    def test_unsupported_canadian_location(self, api_client):
        """9d. Canadian location returns 400 with UNSUPPORTED_LOCATION error code."""
        response = api_client.post(
            self.url,
            {"start": "Toronto, ON", "finish": "New York, NY"},
            format="json",
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error_code"] == "UNSUPPORTED_LOCATION"
        assert "Canada" in data["message"]

    def test_geocoding_location_not_found(self, api_client):
        """9e. Unresolvable location returns 404 with LOCATION_NOT_FOUND error code."""
        with patch.object(
            LocationService,
            "resolve_location",
            side_effect=LocationNotFoundError("Location 'NonExistentPlace999' could not be resolved."),
        ):
            response = api_client.post(
                self.url,
                {"start": "NonExistentPlace999", "finish": "Boston, MA"},
                format="json",
            )
            assert response.status_code == 404
            data = response.json()
            assert data["error_code"] == "LOCATION_NOT_FOUND"

    def test_routing_service_failure(self, api_client, nyc_point, boston_point):
        """9f. Route calculation failure returns 422 Unprocessable Entity."""
        with patch.object(
            LocationService,
            "resolve_location",
            side_effect=[nyc_point, boston_point],
        ):
            with patch.object(
                OsrmRoutingService,
                "calculate_route",
                side_effect=RoutingNotFoundError("No drivable route found between the specified locations."),
            ):
                response = api_client.post(
                    self.url,
                    {"start": "New York, NY", "finish": "Boston, MA"},
                    format="json",
                )
                assert response.status_code == 422
                data = response.json()
                assert data["error_code"] == "ROUTE_NOT_FOUND"

    def test_routing_service_timeout(self, api_client, nyc_point, boston_point):
        """9g. Routing service timeout returns 504 Gateway Timeout."""
        with patch.object(
            LocationService,
            "resolve_location",
            side_effect=[nyc_point, boston_point],
        ):
            with patch.object(
                OsrmRoutingService,
                "calculate_route",
                side_effect=RoutingTimeoutError("Routing service timed out."),
            ):
                response = api_client.post(
                    self.url,
                    {"start": "New York, NY", "finish": "Boston, MA"},
                    format="json",
                )
                assert response.status_code == 504
                data = response.json()
                assert data["error_code"] == "ROUTING_TIMEOUT"

    def test_successful_route_calculation_end_to_end(
        self, api_client, nyc_point, boston_point
    ):
        """9h. Successful route calculation returns 200 OK with complete route details."""
        mock_route_result = RouteResult(
            distance_meters=346000.0,
            distance_miles=215.0,
            duration_seconds=14400.0,
            duration_hours=4.0,
            geometry=RouteGeometry(
                type="LineString",
                coordinates=[[-74.0060, 40.7128], [-71.0589, 42.3601]],
            ),
            start=nyc_point,
            finish=boston_point,
            cached=False,
        )

        with patch.object(
            LocationService,
            "resolve_location",
            side_effect=[nyc_point, boston_point],
        ) as mock_resolve:
            with patch.object(
                OsrmRoutingService,
                "calculate_route",
                return_value=mock_route_result,
            ) as mock_calc:
                response = api_client.post(
                    self.url,
                    {"start": "New York, NY", "finish": "Boston, MA"},
                    format="json",
                )

                assert response.status_code == 200
                data = response.json()

                assert data["status"] == "success"
                assert mock_resolve.call_count == 2
                assert mock_calc.call_count == 1

                # Trip summary assertions
                summary = data["trip_summary"]
                assert summary["start"] == "New York, NY, USA"
                assert summary["finish"] == "Boston, MA, USA"
                assert summary["total_distance_miles"] == 215.0
                assert summary["total_duration_hours"] == 4.0
                assert summary["vehicle_mpg"] == 10.0
                assert summary["vehicle_max_range_miles"] == 500.0
                assert summary["tank_capacity_gallons"] == 50.0
                assert summary["total_gallons_consumed"] == 21.5  # 215.0 / 10.0
                assert summary["total_gallons_purchased"] == 0.0
                assert summary["total_fuel_cost_usd"] == 0.0
                assert summary["fuel_stops_count"] == 0

                # Fuel stops assertions
                assert data["fuel_stops"] == []

                # Route geometry assertions
                assert data["route_geometry"]["type"] == "LineString"
                assert len(data["route_geometry"]["coordinates"]) == 2

                # Metadata assertions
                metadata = data["metadata"]
                assert metadata["routing_provider"] == "OSRM"
                assert metadata["route_cached"] is False
                assert metadata["corridor_miles"] == 5.0

    def test_no_feasible_fuel_plan_returns_422(
        self, api_client, nyc_point, boston_point
    ):
        """9i. If distance exceeds vehicle reach and no stations exist, returns 422 with NO_FEASIBLE_FUEL_PLAN."""
        # 1200 miles with zero candidate stations along corridor
        long_route = RouteResult(
            distance_meters=1931212.8,
            distance_miles=1200.0,
            duration_seconds=64800.0,
            duration_hours=18.0,
            geometry=RouteGeometry(
                type="LineString",
                coordinates=[[-74.0060, 40.7128], [-87.6298, 41.8781]],
            ),
            start=nyc_point,
            finish=boston_point,
            cached=False,
        )

        with patch.object(
            LocationService,
            "resolve_location",
            side_effect=[nyc_point, boston_point],
        ):
            with patch.object(
                OsrmRoutingService,
                "calculate_route",
                return_value=long_route,
            ):
                with patch.object(
                    FuelStationSpatialService,
                    "find_candidates_near_route",
                    return_value=[],  # No stations
                ):
                    response = api_client.post(
                        self.url,
                        {"start": "New York, NY", "finish": "Chicago, IL"},
                        format="json",
                    )
                    assert response.status_code == 422
                    data = response.json()
                    assert data["error_code"] == "NO_FEASIBLE_FUEL_PLAN"
                    assert "No feasible sequence of fuel stops" in data["message"]

    def test_demo_page_loads(self, api_client):
        """Demo page loads successfully with 200 OK and contains Leaflet map."""
        response = api_client.get("/demo/")
        assert response.status_code == 200
        assert b"Spotter Fuel Routing Demo" in response.content
        assert b"leaflet" in response.content

    def test_health_check_endpoint(self, api_client):
        """Health check endpoint returns healthy status."""
        response = api_client.get("/health/")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy", "service": "spotter_fuel_routing"}

    def test_openapi_schema_and_docs_endpoints(self, api_client):
        """Schema and Swagger documentation endpoints return 200 OK."""
        schema_resp = api_client.get("/api/schema/")
        assert schema_resp.status_code == 200

        docs_resp = api_client.get("/api/docs/")
        assert docs_resp.status_code == 200

