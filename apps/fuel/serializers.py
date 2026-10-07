from rest_framework import serializers


class RouteFuelPlanRequestSerializer(serializers.Serializer):
    """Request payload for route and fuel plan calculation."""

    start = serializers.CharField(
        required=True,
        allow_blank=False,
        min_length=2,
        max_length=255,
        help_text="Start location within the USA (e.g. 'New York, NY').",
    )
    finish = serializers.CharField(
        required=True,
        allow_blank=False,
        min_length=2,
        max_length=255,
        help_text="Finish location within the USA (e.g. 'Boston, MA').",
    )


class TripSummarySerializer(serializers.Serializer):
    """Aggregated summary of the calculated trip and fuel consumption."""

    start = serializers.CharField(help_text="Resolved start location name.")
    finish = serializers.CharField(help_text="Resolved destination location name.")
    total_distance_miles = serializers.FloatField(help_text="Total driving distance in miles.")
    total_duration_hours = serializers.FloatField(help_text="Estimated travel duration in hours.")
    vehicle_mpg = serializers.FloatField(default=10.0, help_text="Vehicle fuel economy (10 MPG).")
    vehicle_max_range_miles = serializers.FloatField(default=500.0, help_text="Maximum single-tank range (500 miles).")
    tank_capacity_gallons = serializers.FloatField(default=50.0, help_text="Fuel tank capacity (50 gallons).")
    total_gallons_consumed = serializers.FloatField(help_text="Total fuel burned over the route.")
    total_gallons_purchased = serializers.FloatField(help_text="Total fuel purchased across all stops.")
    total_fuel_cost_usd = serializers.FloatField(help_text="Total cost spent on purchased fuel in USD.")
    fuel_stops_count = serializers.IntegerField(help_text="Number of fuel stops scheduled.")


class FuelStopSerializer(serializers.Serializer):
    """Detailed record for an individual fuel stop along the route."""

    stop_number = serializers.IntegerField(help_text="Sequential stop sequence index (1-based).")
    station_id = serializers.IntegerField(help_text="OPIS Truckstop ID from the dataset.")
    name = serializers.CharField(help_text="Commercial brand or truckstop name.")
    address = serializers.CharField(allow_blank=True, help_text="Street address / highway exit.")
    city = serializers.CharField(help_text="Municipality name.")
    state = serializers.CharField(help_text="Two-letter US state code.")
    latitude = serializers.FloatField(help_text="Station latitude coordinate.")
    longitude = serializers.FloatField(help_text="Station longitude coordinate.")
    price_per_gallon = serializers.FloatField(help_text="Retail fuel price per gallon in USD.")
    distance_along_route_miles = serializers.FloatField(help_text="Route mileage offset from start.")
    distance_from_route_miles = serializers.FloatField(help_text="Perpendicular distance from highway centerline.")
    gallons_purchased = serializers.FloatField(help_text="Gallons of fuel purchased at this stop.")
    cost_usd = serializers.FloatField(help_text="Total dollar cost for this purchase.")


class RouteGeometrySerializer(serializers.Serializer):
    """GeoJSON LineString representation of the driving route."""

    type = serializers.CharField(default="LineString", help_text="GeoJSON geometry type.")
    coordinates = serializers.ListField(
        child=serializers.ListField(child=serializers.FloatField(), min_length=2, max_length=2),
        help_text="List of [longitude, latitude] coordinates describing the driving path.",
    )


class MetadataSerializer(serializers.Serializer):
    """Operational metadata regarding routing and spatial querying."""

    routing_provider = serializers.CharField(default="OSRM", help_text="Upstream routing provider.")
    route_cached = serializers.BooleanField(help_text="Whether route geometry was retrieved from cache.")
    candidate_stations_considered = serializers.IntegerField(help_text="Number of stations within corridor.")
    corridor_miles = serializers.FloatField(default=5.0, help_text="Highway corridor buffer in miles.")


class RouteFuelPlanResponseSerializer(serializers.Serializer):
    """Complete response payload for POST /api/v1/route/fuel-plan/."""

    status = serializers.CharField(default="success")
    trip_summary = TripSummarySerializer()
    fuel_stops = FuelStopSerializer(many=True)
    route_geometry = RouteGeometrySerializer()
    metadata = MetadataSerializer()
