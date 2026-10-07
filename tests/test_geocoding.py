from unittest.mock import MagicMock, patch
import httpx
import pytest

from apps.core.exceptions import (
    GeocodingExternalAPIError,
    GeocodingTimeoutError,
    LocationNotFoundError,
    UnsupportedLocationError,
)
from apps.routing.services.geocoder import LocationService


class TestGeocodingService:
    """Test suite for LocationService (geocoding and geographic boundary enforcement)."""

    def test_valid_geocoding(self, mock_nominatim_nyc):
        """1. Valid geocoding resolves query string to US GeoPoint and caches."""
        service = LocationService()

        with patch("httpx.Client.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_nominatim_nyc
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            point = service.resolve_location("New York, NY")

            assert point.latitude == 40.7128
            assert point.longitude == -74.0060
            assert point.country_code == "us"
            assert point.state == "New York"
            assert mock_get.call_count == 1

            # Subsequent call should hit cache without calling Nominatim
            cached_point = service.resolve_location("New York, NY")
            assert cached_point.latitude == point.latitude
            assert cached_point.longitude == point.longitude
            assert mock_get.call_count == 1  # Still 1 call!

    def test_invalid_location_canadian_query(self):
        """2a. Canadian province in query string is rejected immediately."""
        service = LocationService()
        with pytest.raises(UnsupportedLocationError) as exc_info:
            service.resolve_location("Toronto, ON")
        assert "Canada" in str(exc_info.value)

    def test_invalid_location_canadian_response(self, mock_nominatim_toronto):
        """2b. Canadian location returned from geocoder is rejected."""
        service = LocationService()
        with patch("httpx.Client.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_nominatim_toronto
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            with pytest.raises(UnsupportedLocationError) as exc_info:
                service.resolve_location("Toronto Downtown")
            assert "CA" in str(exc_info.value)

    def test_invalid_location_out_of_bounds(self):
        """2c. Location outside contiguous lower-48 bounding box is rejected."""
        service = LocationService()
        out_of_bounds_response = [
            {
                "lat": "21.3069",  # Honolulu, HI (outside contiguous US)
                "lon": "-157.8583",
                "display_name": "Honolulu, HI, USA",
                "address": {"country_code": "us", "state": "Hawaii"},
            }
        ]
        with patch("httpx.Client.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = out_of_bounds_response
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            with pytest.raises(UnsupportedLocationError) as exc_info:
                service.resolve_location("Honolulu, HI")
            assert "contiguous United States" in str(exc_info.value)

    def test_geocoding_failure_not_found(self):
        """3a. Nominatim returning empty results raises LocationNotFoundError."""
        service = LocationService()
        with patch("httpx.Client.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = []
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            with pytest.raises(LocationNotFoundError):
                service.resolve_location("NonExistentCity123XYZ")

    def test_geocoding_failure_empty_query(self):
        """3b. Empty query raises LocationNotFoundError."""
        service = LocationService()
        with pytest.raises(LocationNotFoundError):
            service.resolve_location("   ")

    def test_geocoding_failure_http_error(self):
        """3c. Nominatim HTTP error raises GeocodingExternalAPIError."""
        service = LocationService()
        with patch("httpx.Client.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_response.text = "Service Unavailable"
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "503", request=MagicMock(), response=mock_response
            )
            mock_get.return_value = mock_response

            with pytest.raises(GeocodingExternalAPIError):
                service.resolve_location("Chicago, IL")

    def test_geocoding_failure_timeout(self):
        """3d. Nominatim timeout raises GeocodingTimeoutError."""
        service = LocationService()
        with patch("httpx.Client.get", side_effect=httpx.TimeoutException("Timeout")):
            with pytest.raises(GeocodingTimeoutError):
                service.resolve_location("Seattle, WA")
