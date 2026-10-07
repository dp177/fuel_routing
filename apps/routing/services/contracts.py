from dataclasses import dataclass, field


@dataclass(frozen=True)
class GeoPoint:
    """Represents a validated geographic point."""

    latitude: float
    longitude: float
    display_name: str = ""
    country_code: str = "us"
    state: str = ""

    def to_dict(self) -> dict:
        return {
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "display_name": self.display_name,
            "country_code": self.country_code,
            "state": self.state,
        }


@dataclass
class RouteGeometry:
    """Represents GeoJSON LineString geometry."""

    type: str = "LineString"
    coordinates: list[list[float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "coordinates": self.coordinates,
        }


@dataclass
class RouteResult:
    """Result of a calculated driving route."""

    distance_meters: float
    distance_miles: float
    duration_seconds: float
    duration_hours: float
    geometry: RouteGeometry
    start: GeoPoint
    finish: GeoPoint
    cached: bool = False

    def to_dict(self) -> dict:
        return {
            "start": self.start.to_dict(),
            "finish": self.finish.to_dict(),
            "distance_miles": round(self.distance_miles, 2),
            "distance_meters": round(self.distance_meters, 1),
            "duration_hours": round(self.duration_hours, 2),
            "duration_seconds": round(self.duration_seconds, 1),
            "geometry": self.geometry.to_dict(),
            "cached": self.cached,
        }
