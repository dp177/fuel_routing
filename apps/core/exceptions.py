from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


class AppException(Exception):
    """Base application exception with status code and error code."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code = "INTERNAL_SERVER_ERROR"

    def __init__(self, message: str, details: dict | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code


class LocationNotFoundError(AppException):
    """Raised when a geocoding service cannot find coordinates for a query."""

    status_code = status.HTTP_404_NOT_FOUND
    error_code = "LOCATION_NOT_FOUND"


class UnsupportedLocationError(AppException):
    """Raised when a location is outside the supported contiguous US geography."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "UNSUPPORTED_LOCATION"


class GeocodingTimeoutError(AppException):
    """Raised when an external geocoding service times out."""

    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    error_code = "GEOCODING_TIMEOUT"


class GeocodingExternalAPIError(AppException):
    """Raised when an external geocoding service returns an HTTP error."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "GEOCODING_GATEWAY_ERROR"


class RoutingNotFoundError(AppException):
    """Raised when no drivable route exists between start and finish points."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    error_code = "ROUTE_NOT_FOUND"


class RoutingTimeoutError(AppException):
    """Raised when the routing engine times out."""

    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    error_code = "ROUTING_TIMEOUT"


class RoutingExternalAPIError(AppException):
    """Raised when the external routing engine returns an HTTP error."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "ROUTING_GATEWAY_ERROR"


class NoFeasibleFuelPlanError(AppException):
    """Raised when no valid sequence of reachable fuel stops can cover the route."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    error_code = "NO_FEASIBLE_FUEL_PLAN"


def custom_exception_handler(exc, context):
    """Custom DRF exception handler providing clean, standardized error responses."""
    if isinstance(exc, AppException):
        return Response(
            {
                "status": "error",
                "error_code": exc.error_code,
                "message": exc.message,
                "details": exc.details,
            },
            status=exc.status_code,
        )

    response = exception_handler(exc, context)

    if response is not None:
        error_code = "VALIDATION_ERROR" if response.status_code == 400 else "REQUEST_ERROR"
        return Response(
            {
                "status": "error",
                "error_code": error_code,
                "message": "Request validation failed." if response.status_code == 400 else str(exc),
                "details": response.data,
            },
            status=response.status_code,
        )

    return None
