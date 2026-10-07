import logging
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.fuel.serializers import (
    RouteFuelPlanRequestSerializer,
    RouteFuelPlanResponseSerializer,
)
from apps.fuel.services.optimizer import FuelOptimizer
from apps.fuel.services.spatial_service import FuelStationSpatialService
from apps.routing.services.geocoder import LocationService
from apps.routing.services.osrm import OsrmRoutingService

logger = logging.getLogger(__name__)


class RouteFuelPlanView(APIView):
    """API endpoint to calculate driving route and cost-effective fuel stops between two US locations."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.location_service = LocationService()
        self.routing_service = OsrmRoutingService()
        self.spatial_service = FuelStationSpatialService.get_instance()
        self.fuel_optimizer = FuelOptimizer()

    @extend_schema(
        request=RouteFuelPlanRequestSerializer,
        responses={
            200: RouteFuelPlanResponseSerializer,
            400: OpenApiResponse(
                description="Unsupported Location or Validation Error",
                examples=[
                    OpenApiExample(
                        "Unsupported Location",
                        value={
                            "status": "error",
                            "error_code": "UNSUPPORTED_LOCATION",
                            "message": "Location 'Toronto, ON' is in Canada. Only contiguous US locations are supported.",
                            "details": {},
                        },
                    )
                ],
            ),
            404: OpenApiResponse(
                description="Location Not Found",
                examples=[
                    OpenApiExample(
                        "Location Not Found",
                        value={
                            "status": "error",
                            "error_code": "LOCATION_NOT_FOUND",
                            "message": "Location 'NonExistentCityXYZ' could not be resolved.",
                            "details": {},
                        },
                    )
                ],
            ),
            422: OpenApiResponse(
                description="No Feasible Fuel Plan",
                examples=[
                    OpenApiExample(
                        "No Feasible Fuel Plan",
                        value={
                            "status": "error",
                            "error_code": "NO_FEASIBLE_FUEL_PLAN",
                            "message": "No feasible sequence of fuel stops can cover the route within the vehicle's 500-mile range.",
                            "details": {},
                        },
                    )
                ],
            ),
        },
        summary="Calculate US Route and Fuel Stop Plan",
        description=(
            "Accepts start and finish locations within the contiguous United States, resolves coordinates via Nominatim, "
            "computes the highway route using OSRM, filters candidate fuel stations along a 5-mile highway corridor, "
            "and calculates the optimal sequence of fuel purchases under vehicle physical constraints (500-mile range, 10 MPG)."
        ),
        examples=[
            OpenApiExample(
                "Valid Route Request (NY to Boston)",
                request_only=True,
                value={
                    "start": "New York, NY",
                    "finish": "Boston, MA",
                },
            ),
            OpenApiExample(
                "Valid Route Request (NY to Chicago)",
                request_only=True,
                value={
                    "start": "New York, NY",
                    "finish": "Chicago, IL",
                },
            ),
            OpenApiExample(
                "Successful Route and Fuel Plan Response",
                response_only=True,
                value={
                    "status": "success",
                    "trip_summary": {
                        "start": "New York, NY, USA",
                        "finish": "Boston, MA, USA",
                        "total_distance_miles": 213.52,
                        "total_duration_hours": 4.68,
                        "vehicle_mpg": 10.0,
                        "vehicle_max_range_miles": 500.0,
                        "tank_capacity_gallons": 50.0,
                        "total_gallons_consumed": 21.35,
                        "total_gallons_purchased": 0.0,
                        "total_fuel_cost_usd": 0.0,
                        "fuel_stops_count": 0,
                    },
                    "fuel_stops": [],
                    "route_geometry": {
                        "type": "LineString",
                        "coordinates": [[-74.006, 40.7128], [-71.0589, 42.3601]],
                    },
                    "metadata": {
                        "routing_provider": "OSRM",
                        "route_cached": False,
                        "candidate_stations_considered": 42,
                        "corridor_miles": 5.0,
                    },
                },
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        serializer = RouteFuelPlanRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        start_query = serializer.validated_data["start"]
        finish_query = serializer.validated_data["finish"]

        logger.info("Processing route request: %s -> %s", start_query, finish_query)

        # 1. Resolve locations to coordinates (geocodes ONLY start and finish)
        start_point = self.location_service.resolve_location(start_query)
        finish_point = self.location_service.resolve_location(finish_query)

        # 2. Compute driving route via single OSRM request (with coordinate caching)
        route_result = self.routing_service.calculate_route(start_point, finish_point)

        # 3. Discover fuel station candidates within the 5-mile highway corridor
        candidates = self.spatial_service.find_candidates_near_route(
            route_result.geometry.to_dict(),
            corridor_miles=5.0,
        )
        logger.info("Discovered %d fuel stations along route corridor", len(candidates))

        # 4. Optimize fuel stop schedule and purchases
        fuel_plan = self.fuel_optimizer.optimize(
            total_distance_miles=route_result.distance_miles,
            candidate_stations=candidates,
            initial_fuel=50.0,
        )

        formatted_stops = [
            {
                "stop_number": stop.stop_number,
                "station_id": stop.station_id,
                "name": stop.name,
                "address": stop.address,
                "city": stop.city,
                "state": stop.state,
                "latitude": stop.latitude,
                "longitude": stop.longitude,
                "price_per_gallon": round(stop.price_per_gallon, 4),
                "distance_along_route_miles": round(stop.distance_along_route_miles, 2),
                "distance_from_route_miles": round(stop.distance_from_route_miles, 2),
                "gallons_purchased": round(stop.gallons_purchased, 2),
                "cost_usd": round(stop.cost_usd, 2),
            }
            for stop in fuel_plan.fuel_stops
        ]

        response_data = {
            "status": "success",
            "trip_summary": {
                "start": start_point.display_name,
                "finish": finish_point.display_name,
                "total_distance_miles": round(route_result.distance_miles, 2),
                "total_duration_hours": round(route_result.duration_hours, 2),
                "vehicle_mpg": fuel_plan.summary["vehicle_mpg"],
                "vehicle_max_range_miles": fuel_plan.summary["max_range_miles"],
                "tank_capacity_gallons": fuel_plan.summary["tank_capacity_gallons"],
                "total_gallons_consumed": round(fuel_plan.summary["total_gallons_consumed"], 2),
                "total_gallons_purchased": round(fuel_plan.summary["total_gallons_purchased"], 2),
                "total_fuel_cost_usd": round(fuel_plan.summary["total_cost_usd"], 2),
                "fuel_stops_count": fuel_plan.summary["total_stops"],
            },
            "fuel_stops": formatted_stops,
            "route_geometry": {
                "type": route_result.geometry.type,
                "coordinates": route_result.geometry.coordinates,
            },
            "metadata": {
                "routing_provider": "OSRM",
                "route_cached": route_result.cached,
                "candidate_stations_considered": len(candidates),
                "corridor_miles": 5.0,
            },
        }

        return Response(response_data, status=status.HTTP_200_OK)
