import hashlib
import logging
import re
import httpx
from django.conf import settings
from django.core.cache import cache

from apps.core.exceptions import (
    GeocodingExternalAPIError,
    GeocodingTimeoutError,
    LocationNotFoundError,
    UnsupportedLocationError,
)
from apps.routing.services.contracts import GeoPoint

logger = logging.getLogger(__name__)

# Geographic boundaries for Contiguous United States (Lower 48)
US_MIN_LAT = 24.0
US_MAX_LAT = 50.0
US_MIN_LON = -125.0
US_MAX_LON = -66.0

CANADIAN_PROVINCE_CODES = {
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"
}


class LocationService:
    """Geocoding and geographic boundary enforcement service for start and finish points.

    Queries Nominatim (OpenStreetMap) with strict US geographical boundary enforcement
    and persistent caching to prevent redundant external API hits.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        user_agent: str | None = None,
    ):
        self.base_url = (
            base_url or getattr(settings, "NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org")
        ).rstrip("/")
        self.timeout = timeout or getattr(settings, "NOMINATIM_TIMEOUT", 8.0)
        self.user_agent = user_agent or getattr(
            settings, "NOMINATIM_USER_AGENT", "SpotterFuelRoutingAssessment/1.0"
        )
        self.cache_ttl = getattr(settings, "GEOCODING_CACHE_TTL", 604800)

    def _normalize_query(self, query: str) -> str:
        """Strip and normalize query string."""
        return " ".join(query.strip().split())

    def _generate_cache_key(self, query: str) -> str:
        """Deterministic, backend-safe MD5 cache key."""
        clean = self._normalize_query(query).lower()
        key_hash = hashlib.md5(clean.encode("utf-8")).hexdigest()
        return f"geocode:{key_hash}"

    def _pre_validate_canadian_input(self, query: str) -> None:
        """Fast-fail if query explicitly specifies Canadian province or country."""
        normalized = query.upper()
        if "CANADA" in normalized:
            raise UnsupportedLocationError(
                f"Location '{query}' is in Canada. Only locations within the USA are supported."
            )
        # Check trailing state/province code, e.g. 'Toronto, ON' or 'Vancouver, BC'
        match = re.search(r",\s*([A-Z]{2})\b", normalized)
        if match:
            code = match.group(1)
            if code in CANADIAN_PROVINCE_CODES:
                raise UnsupportedLocationError(
                    f"Location '{query}' is in Canada ({code}). Only locations within the USA are supported."
                )

    def _validate_us_geography(self, point: GeoPoint, original_query: str) -> None:
        """Validate country code and lower-48 contiguous US bounding box."""
        if point.country_code != "us":
            raise UnsupportedLocationError(
                f"Location '{original_query}' is resolved to country '{point.country_code.upper()}'. "
                f"Only locations within the United States are supported."
            )

        if not (US_MIN_LAT <= point.latitude <= US_MAX_LAT and US_MIN_LON <= point.longitude <= US_MAX_LON):
            raise UnsupportedLocationError(
                f"Location '{original_query}' ({point.latitude:.4f}, {point.longitude:.4f}) is outside "
                f"the contiguous United States bounding area."
            )

    def resolve_location(self, query: str) -> GeoPoint:
        """Resolve a location query string to a validated US GeoPoint.

        Args:
            query: Location string (e.g. 'New York, NY', 'Austin, TX').

        Returns:
            Validated GeoPoint within the contiguous US.

        Raises:
            LocationNotFoundError: If query cannot be found.
            UnsupportedLocationError: If location is in Canada or outside lower-48 US.
            GeocodingTimeoutError: If geocoding request times out.
            GeocodingExternalAPIError: If external service returns an error.
        """
        clean_query = self._normalize_query(query)
        if not clean_query:
            raise LocationNotFoundError("Location query cannot be empty.")

        # Heuristic pre-check for Canadian queries
        self._pre_validate_canadian_input(clean_query)

        cache_key = self._generate_cache_key(clean_query)
        cached = cache.get(cache_key)
        if cached:
            logger.info("Geocode cache HIT for '%s'", clean_query)
            point = GeoPoint(
                latitude=cached["latitude"],
                longitude=cached["longitude"],
                display_name=cached["display_name"],
                country_code=cached["country_code"],
                state=cached.get("state", ""),
            )
            self._validate_us_geography(point, clean_query)
            return point

        logger.info("Geocode cache MISS for '%s'; querying Nominatim", clean_query)

        url = f"{self.base_url}/search"
        params = {
            "q": clean_query,
            "format": "json",
            "addressdetails": "1",
            "limit": "1",
        }
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            logger.error("Geocoding request timed out for '%s': %s", clean_query, exc)
            raise GeocodingTimeoutError(f"Geocoding service timed out for '{clean_query}'.") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("Geocoding HTTP error %s for '%s': %s", exc.response.status_code, clean_query, exc.response.text)
            raise GeocodingExternalAPIError(
                f"Geocoding service returned HTTP status {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            logger.error("Geocoding network error for '%s': %s", clean_query, exc)
            raise GeocodingExternalAPIError(f"Network error connecting to geocoding service: {exc}") from exc

        if not data or not isinstance(data, list):
            raise LocationNotFoundError(f"Location '{clean_query}' could not be resolved.")

        top_match = data[0]
        address = top_match.get("address", {})
        country_code = address.get("country_code", "").lower()
        state = address.get("state", "")

        point = GeoPoint(
            latitude=float(top_match["lat"]),
            longitude=float(top_match["lon"]),
            display_name=top_match.get("display_name", clean_query),
            country_code=country_code,
            state=state,
        )

        # Validate US bounds
        self._validate_us_geography(point, clean_query)

        # Store in cache
        cache.set(
            cache_key,
            {
                "latitude": point.latitude,
                "longitude": point.longitude,
                "display_name": point.display_name,
                "country_code": point.country_code,
                "state": point.state,
            },
            timeout=self.cache_ttl,
        )

        return point
