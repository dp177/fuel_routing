from abc import ABC, abstractmethod
from apps.routing.services.contracts import GeoPoint, RouteResult


class BaseRoutingService(ABC):
    """Abstract interface for external routing providers.

    Note: The routing service is decoupled from fuel logic and knows nothing
    about fuel stations or optimization.
    """

    @abstractmethod
    def calculate_route(self, start: GeoPoint, finish: GeoPoint) -> RouteResult:
        """Calculate a driving route between two points.

        Args:
            start: Start geographic point.
            finish: Finish geographic point.

        Returns:
            RouteResult with distance, duration, and GeoJSON LineString geometry.

        Raises:
            RoutingNotFoundError: If no drivable route exists.
            RoutingTimeoutError: If the external routing service times out.
            RoutingExternalAPIError: If the external service encounters an error.
        """
        pass
