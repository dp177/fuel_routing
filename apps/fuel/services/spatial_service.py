import math
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import shapely
from shapely.geometry import LineString, Point

from apps.fuel.models import FuelStation

logger = logging.getLogger(__name__)

EARTH_RADIUS_MILES = 3958.8
MILES_PER_LAT_DEGREE = 69.0


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points in miles."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_MILES * math.asin(math.sqrt(max(0.0, min(1.0, a))))


@dataclass(frozen=True)
class FuelStationCandidate:
    """Represents a fuel station candidate projected onto a driving route."""

    station_id: int
    name: str
    address: str
    city: str
    state: str
    price_per_gallon: Decimal
    latitude: float
    longitude: float
    distance_along_route_miles: float
    distance_from_route_miles: float
    coordinate_source: str = "city_centroid"
    rack_id: int | None = None

    def to_dict(self) -> dict:
        return {
            "station_id": self.station_id,
            "name": self.name,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "price_per_gallon": float(self.price_per_gallon),
            "latitude": round(self.latitude, 5),
            "longitude": round(self.longitude, 5),
            "distance_along_route_miles": round(self.distance_along_route_miles, 2),
            "distance_from_route_miles": round(self.distance_from_route_miles, 2),
            "coordinate_source": self.coordinate_source,
            "rack_id": self.rack_id,
        }


class FuelStationSpatialService:
    """In-memory spatial index and route corridor projection service for fuel stations.

    Uses Shapely STRtree (R-tree) for sub-millisecond candidate pre-filtering,
    followed by accurate geodesic segment projection to compute exact cross-track
    distances and mileage along the route.
    """

    _instance: "FuelStationSpatialService | None" = None

    def __init__(self):
        self._tree: shapely.STRtree | None = None
        self._stations: list[dict[str, Any]] = []

    @classmethod
    def get_instance(cls) -> "FuelStationSpatialService":
        """Singleton pattern for application-wide spatial index caching."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def build_index(self, stations: list[dict[str, Any] | FuelStation] | None = None) -> int:
        """Build or reload the in-memory STRtree spatial index.

        Args:
            stations: Optional explicit station list (useful for isolated unit testing).
                      If None, loads all stations with valid coordinates from the database.

        Returns:
            Number of indexed stations.
        """
        if stations is None:
            qs = FuelStation.objects.filter(latitude__isnull=False, longitude__isnull=False)
            station_records = [
                {
                    "station_id": s.station_id,
                    "name": s.name,
                    "address": s.address,
                    "city": s.city,
                    "state": s.state,
                    "rack_id": s.rack_id,
                    "price_per_gallon": s.price_per_gallon,
                    "latitude": s.latitude,
                    "longitude": s.longitude,
                    "coordinate_source": s.coordinate_source,
                }
                for s in qs
            ]
        else:
            station_records = []
            for s in stations:
                if isinstance(s, FuelStation):
                    if s.latitude is not None and s.longitude is not None:
                        station_records.append(
                            {
                                "station_id": s.station_id,
                                "name": s.name,
                                "address": s.address,
                                "city": s.city,
                                "state": s.state,
                                "rack_id": s.rack_id,
                                "price_per_gallon": s.price_per_gallon,
                                "latitude": s.latitude,
                                "longitude": s.longitude,
                                "coordinate_source": s.coordinate_source,
                            }
                        )
                elif isinstance(s, dict):
                    if s.get("latitude") is not None and s.get("longitude") is not None:
                        station_records.append(s)

        if not station_records:
            self._tree = None
            self._stations = []
            logger.warning("No fuel stations available to index.")
            return 0

        points = [Point(s["longitude"], s["latitude"]) for s in station_records]
        self._tree = shapely.STRtree(points)
        self._stations = station_records
        logger.info("STRtree indexed %d fuel stations successfully.", len(self._stations))
        return len(self._stations)

    def is_indexed(self) -> bool:
        return self._tree is not None and len(self._stations) > 0

    def invalidate_index(self) -> None:
        """Reset the cached spatial index."""
        self._tree = None
        self._stations = []

    def _ensure_indexed(self) -> None:
        """Lazy-load the spatial index from the database if not already loaded."""
        if not self.is_indexed():
            self.build_index()

    def find_candidates_near_route(
        self,
        route_geometry: dict | list,
        corridor_miles: float = 5.0,
    ) -> list[FuelStationCandidate]:
        """Find and project candidate fuel stations within corridor_miles of a route geometry.

        Args:
            route_geometry: GeoJSON dict (type='LineString', coordinates=[[lon, lat], ...])
                            or a raw coordinate list.
            corridor_miles: Maximum allowable perpendicular distance from highway corridor.

        Returns:
            List of FuelStationCandidate sorted by distance_along_route_miles.
        """
        self._ensure_indexed()
        if not self._tree or not self._stations:
            return []

        # Extract coordinates
        if isinstance(route_geometry, dict):
            coords = route_geometry.get("coordinates", [])
        elif isinstance(route_geometry, list):
            coords = route_geometry
        else:
            raise ValueError("route_geometry must be a GeoJSON LineString dictionary or coordinate list.")

        if len(coords) < 2:
            return []

        # Construct Shapely LineString (coordinates in [lon, lat])
        route_line = LineString(coords)

        # Conservative degree buffer for lower-48 US latitudes (min cos(50 deg) ~ 0.64)
        degree_buffer = (corridor_miles / (MILES_PER_LAT_DEGREE * 0.64)) * 1.15
        buffered_envelope = route_line.buffer(degree_buffer)

        # 1. High-speed R-Tree query for candidate stations in the corridor envelope
        candidate_indices = self._tree.query(buffered_envelope, predicate="intersects")
        if len(candidate_indices) == 0:
            return []

        # 2. Precompute cumulative road mileage along the route segments
        m = len(coords)
        cum_miles = [0.0]
        segment_lengths = []
        for i in range(m - 1):
            lon1, lat1 = coords[i]
            lon2, lat2 = coords[i + 1]
            seg_len = haversine_miles(lat1, lon1, lat2, lon2)
            segment_lengths.append(seg_len)
            cum_miles.append(cum_miles[-1] + seg_len)

        # Build chunk bounding boxes for high-speed spatial pruning
        chunk_size = 64
        deg_pad_lat = (corridor_miles / MILES_PER_LAT_DEGREE) * 1.15
        deg_pad_lon = (corridor_miles / (MILES_PER_LAT_DEGREE * 0.60)) * 1.15

        chunks = []
        for chunk_start in range(0, m - 1, chunk_size):
            chunk_end = min(m - 1, chunk_start + chunk_size)
            c_lats = [coords[k][1] for k in range(chunk_start, chunk_end + 1)]
            c_lons = [coords[k][0] for k in range(chunk_start, chunk_end + 1)]
            min_lat = min(c_lats) - deg_pad_lat
            max_lat = max(c_lats) + deg_pad_lat
            min_lon = min(c_lons) - deg_pad_lon
            max_lon = max(c_lons) + deg_pad_lon
            chunks.append((min_lat, max_lat, min_lon, max_lon, chunk_start, chunk_end))

        # 3. Project candidate stations onto the route polyline
        candidates: list[FuelStationCandidate] = []
        seen_station_ids: set[int] = set()

        for idx in candidate_indices:
            station = self._stations[idx]
            station_id = station["station_id"]

            # Avoid processing duplicate station IDs
            if station_id in seen_station_ids:
                continue

            s_lat = station["latitude"]
            s_lon = station["longitude"]

            best_from_miles = float("inf")
            best_along_miles = 0.0

            # Scan only route chunks whose padded envelope encompasses the station
            for min_lat, max_lat, min_lon, max_lon, c_start, c_end in chunks:
                if not (min_lat <= s_lat <= max_lat and min_lon <= s_lon <= max_lon):
                    continue

                for i in range(c_start, c_end):
                    lon1, lat1 = coords[i]
                    lon2, lat2 = coords[i + 1]
                    lat_mid = (lat1 + lat2) / 2.0
                    cos_lat = math.cos(math.radians(lat_mid))

                    dx = (lon2 - lon1) * MILES_PER_LAT_DEGREE * cos_lat
                    dy = (lat2 - lat1) * MILES_PER_LAT_DEGREE
                    seg_len_sq = dx * dx + dy * dy

                    px = (s_lon - lon1) * MILES_PER_LAT_DEGREE * cos_lat
                    py = (s_lat - lat1) * MILES_PER_LAT_DEGREE

                    if seg_len_sq > 0:
                        t = max(0.0, min(1.0, (px * dx + py * dy) / seg_len_sq))
                    else:
                        t = 0.0

                    qx = t * dx
                    qy = t * dy
                    dist_miles = math.hypot(px - qx, py - qy)

                    if dist_miles < best_from_miles:
                        best_from_miles = dist_miles
                        best_along_miles = cum_miles[i] + t * segment_lengths[i]

            # Enforce strict corridor threshold
            if best_from_miles <= corridor_miles:
                seen_station_ids.add(station_id)
                candidates.append(
                    FuelStationCandidate(
                        station_id=station_id,
                        name=station["name"],
                        address=station["address"],
                        city=station["city"],
                        state=station["state"],
                        rack_id=station.get("rack_id"),
                        price_per_gallon=station["price_per_gallon"],
                        latitude=s_lat,
                        longitude=s_lon,
                        distance_along_route_miles=round(best_along_miles, 2),
                        distance_from_route_miles=round(best_from_miles, 2),
                        coordinate_source=station.get("coordinate_source", "city_centroid"),
                    )
                )

        # 4. Sort candidates monotonically by mileage along the route
        candidates.sort(key=lambda c: c.distance_along_route_miles)
        return candidates
