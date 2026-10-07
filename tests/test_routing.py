from unittest.mock import MagicMock, patch
import httpx
import pytest

from apps.core.exceptions import (
    RoutingExternalAPIError,
    RoutingNotFoundError,
    RoutingTimeoutError,
)
from apps.routing.services.contracts import GeoPoint
from apps.routing.services.osrm import OsrmRoutingService


class TestOsrmRoutingService:
    """Test suite for OsrmRoutingService."""

    def test_successful_osrm_route(self, nyc_point, boston_point, mock_osrm_nyc_to_boston):
        """4. Successful OSRM route returns distance, duration, and GeoJSON LineString."""
        service = OsrmRoutingService()

        with patch("httpx.Client.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_osrm_nyc_to_boston
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            route = service.calculate_route(nyc_point, boston_point)

            assert mock_get.call_count == 1
            # 346000 meters * 0.000621371 = ~214.99 miles
            assert round(route.distance_miles, 1) == 215.0
            # 14400 seconds / 3600 = 4.0 hours
            assert round(route.duration_hours, 1) == 4.0
            assert route.geometry.type == "LineString"
            assert len(route.geometry.coordinates) == 4
            assert route.cached is False

    def test_osrm_failure_no_route(self, nyc_point, boston_point):
        """5a. OSRM returning code 'NoRoute' raises RoutingNotFoundError."""
        service = OsrmRoutingService()
        no_route_payload = {"code": "NoRoute", "message": "No route found"}

        with patch("httpx.Client.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = no_route_payload
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            with pytest.raises(RoutingNotFoundError) as exc_info:
                service.calculate_route(nyc_point, boston_point)
            assert "No drivable route found" in str(exc_info.value)

    def test_osrm_failure_http_500(self, nyc_point, boston_point):
        """5b. OSRM returning HTTP 500 raises RoutingExternalAPIError."""
        service = OsrmRoutingService()

        with patch("httpx.Client.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "500", request=MagicMock(), response=mock_response
            )
            mock_get.return_value = mock_response

            with pytest.raises(RoutingExternalAPIError) as exc_info:
                service.calculate_route(nyc_point, boston_point)
            assert "HTTP status 500" in str(exc_info.value)

    def test_osrm_failure_network_error(self, nyc_point, boston_point):
        """5c. Network failure raises RoutingExternalAPIError."""
        service = OsrmRoutingService()

        with patch("httpx.Client.get", side_effect=httpx.RequestError("Network unreachable")):
            with pytest.raises(RoutingExternalAPIError) as exc_info:
                service.calculate_route(nyc_point, boston_point)
            assert "Network error" in str(exc_info.value)

    def test_osrm_timeout(self, nyc_point, boston_point):
        """6. OSRM timeout raises RoutingTimeoutError."""
        service = OsrmRoutingService()

        with patch("httpx.Client.get", side_effect=httpx.TimeoutException("Read timed out")):
            with pytest.raises(RoutingTimeoutError) as exc_info:
                service.calculate_route(nyc_point, boston_point)
            assert "timed out" in str(exc_info.value)

    def test_route_cache_hit_and_miss(self, nyc_point, boston_point, mock_osrm_nyc_to_boston):
        """7 & 8. Route cache miss triggers external call; cache hit avoids external call."""
        service = OsrmRoutingService()

        with patch("httpx.Client.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_osrm_nyc_to_boston
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            # 1. First call: CACHE MISS (calls OSRM)
            route1 = service.calculate_route(nyc_point, boston_point)
            assert route1.cached is False
            assert mock_get.call_count == 1

            # 2. Second call: CACHE HIT (uses cache, zero additional HTTP calls)
            route2 = service.calculate_route(nyc_point, boston_point)
            assert route2.cached is True
            assert mock_get.call_count == 1  # Still 1!
            assert route2.distance_miles == route1.distance_miles

            # 3. Third call with different destination: CACHE MISS
            philly_point = GeoPoint(39.9526, -75.1652, "Philadelphia, PA", "us", "PA")
            service.calculate_route(nyc_point, philly_point)
            assert mock_get.call_count == 2
