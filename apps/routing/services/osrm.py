import hashlib
import logging
import httpx
from django.conf import settings
from django.core.cache import cache

from apps.core.exceptions import (
    RoutingExternalAPIError,
    RoutingNotFoundError,
    RoutingTimeoutError,
)
from apps.routing.services.base import BaseRoutingService
from apps.routing.services.contracts import GeoPoint, RouteGeometry, RouteResult

logger = logging.getLogger(__name__)

METERS_TO_MILES = 0.000621371
SECONDS_TO_HOURS = 1.0 / 3600.0


class OsrmRoutingService(BaseRoutingService):
    """OSRM (Open Source Routing Machine) routing client.

    Executes a single HTTP request to compute driving routes and GeoJSON geometry.
    Supports deterministic coordinate-based caching to prevent redundant external calls.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        profile: str = "driving",
    ):
        self.base_url = (base_url or getattr(settings, "OSRM_BASE_URL", "https://router.project-osrm.org")).rstrip("/")
        self.timeout = timeout or getattr(settings, "OSRM_TIMEOUT", 10.0)
        self.profile = profile
        self.cache_ttl = getattr(settings, "ROUTING_CACHE_TTL", 86400)

    def _generate_cache_key(self, start: GeoPoint, finish: GeoPoint) -> str:
        """Deterministic, backend-safe cache key based on rounded coordinates and profile."""
        start_lat = f"{start.latitude:.5f}"
        start_lon = f"{start.longitude:.5f}"
        finish_lat = f"{finish.latitude:.5f}"
        finish_lon = f"{finish.longitude:.5f}"
        raw_key = f"{self.profile}:{start_lat},{start_lon}:{finish_lat},{finish_lon}"
        key_hash = hashlib.md5(raw_key.encode("utf-8")).hexdigest()
        return f"route:{key_hash}"

    def calculate_route(self, start: GeoPoint, finish: GeoPoint) -> RouteResult:
        """Calculate driving route between start and finish points.

        Requires exactly ONE OSRM routing request on cache miss.
        """
        cache_key = self._generate_cache_key(start, finish)
        cached_data = cache.get(cache_key)

        if cached_data is not None:
            logger.info("Route cache HIT for %s -> %s", start.display_name, finish.display_name)
            return RouteResult(
                distance_meters=cached_data["distance_meters"],
                distance_miles=cached_data["distance_miles"],
                duration_seconds=cached_data["duration_seconds"],
                duration_hours=cached_data["duration_hours"],
                geometry=RouteGeometry(
                    type=cached_data["geometry"]["type"],
                    coordinates=cached_data["geometry"]["coordinates"],
                ),
                start=start,
                finish=finish,
                cached=True,
            )

        logger.info("Route cache MISS for %s -> %s; querying OSRM", start.display_name, finish.display_name)

        # OSRM coordinate sequence: {longitude},{latitude}
        coords_str = f"{start.longitude},{start.latitude};{finish.longitude},{finish.latitude}"
        url = f"{self.base_url}/route/v1/{self.profile}/{coords_str}"
        params = {
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            logger.error("OSRM request timed out: %s", exc)
            raise RoutingTimeoutError("Routing service timed out while calculating driving path.") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("OSRM HTTP error %s: %s", exc.response.status_code, exc.response.text)
            raise RoutingExternalAPIError(
                f"External routing service returned HTTP status {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            logger.error("OSRM network failure: %s", exc)
            raise RoutingExternalAPIError(f"Network error connecting to routing service: {exc}") from exc

        code = data.get("code")
        if code != "Ok":
            if code == "NoRoute":
                raise RoutingNotFoundError("No drivable route found between the specified locations.")
            raise RoutingExternalAPIError(
                f"Routing service returned error code '{code}': {data.get('message', 'No details provided')}."
            )

        routes = data.get("routes")
        if not routes:
            raise RoutingNotFoundError("Routing service returned no route paths.")

        primary_route = routes[0]
        distance_meters = float(primary_route.get("distance", 0.0))
        duration_seconds = float(primary_route.get("duration", 0.0))
        geometry_data = primary_route.get("geometry", {})

        distance_miles = distance_meters * METERS_TO_MILES
        duration_hours = duration_seconds * SECONDS_TO_HOURS

        route_geometry = RouteGeometry(
            type=geometry_data.get("type", "LineString"),
            coordinates=geometry_data.get("coordinates", []),
        )

        result = RouteResult(
            distance_meters=distance_meters,
            distance_miles=distance_miles,
            duration_seconds=duration_seconds,
            duration_hours=duration_hours,
            geometry=route_geometry,
            start=start,
            finish=finish,
            cached=False,
        )

        # Store in cache
        cache.set(
            cache_key,
            {
                "distance_meters": distance_meters,
                "distance_miles": distance_miles,
                "duration_seconds": duration_seconds,
                "duration_hours": duration_hours,
                "geometry": route_geometry.to_dict(),
            },
            timeout=self.cache_ttl,
        )

        return result
