import json
import os
import time
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

from rest_framework.test import APIClient

client = APIClient()


def evaluate_api_route(origin, destination):
    print("\n" + "=" * 75)
    print(f"EVALUATING END-TO-END API ROUTE: {origin} -> {destination}")
    print("=" * 75)
    t0 = time.perf_counter()
    response = client.post(
        "/api/v1/route/fuel-plan/",
        {"start": origin, "finish": destination},
        format="json",
    )
    t_total = (time.perf_counter() - t0) * 1000

    assert response.status_code == 200, f"Failed with {response.status_code}: {response.json()}"
    data = response.json()

    summary = data["trip_summary"]
    stops = data["fuel_stops"]
    meta = data["metadata"]
    geom = data["route_geometry"]

    print(f"Status Code:            {response.status_code}")
    print(f"Total API Latency:      {t_total:.1f} ms")
    print(f"Resolved Origin:        {summary['start']}")
    print(f"Resolved Destination:   {summary['finish']}")
    print(f"Total Distance:         {summary['total_distance_miles']:.2f} miles")
    print(f"Estimated Duration:     {summary['total_duration_hours']:.2f} hours")
    print(f"Route Points:           {len(geom['coordinates']):,}")
    print(f"Candidate Stations:     {meta['candidate_stations_considered']}")
    print(f"Route Cached:           {meta['route_cached']}")
    print(f"Fuel Stops Count:       {summary['fuel_stops_count']}")
    print(f"Gallons Consumed:       {summary['total_gallons_consumed']:.2f} gal")
    print(f"Gallons Purchased:      {summary['total_gallons_purchased']:.2f} gal")
    print(f"Total Fuel Cost (USD):  ${summary['total_fuel_cost_usd']:.2f}")

    print("\n--- DETAILED FUEL STOPS ---")
    if not stops:
        print("  (0 stops scheduled: Full 50-gallon tank covers entire route!)")
    else:
        prev_mile = 0.0
        for s in stops:
            leg_len = s["distance_along_route_miles"] - prev_mile
            print(f"  Stop #{s['stop_number']}: [{s['station_id']}] {s['name']} ({s['city']}, {s['state']})")
            print(f"    Mile: {s['distance_along_route_miles']:.1f} mi (leg: {leg_len:.1f} mi) | Off-route: {s['distance_from_route_miles']:.2f} mi")
            print(f"    Price: ${s['price_per_gallon']:.4f}/gal | Purchased: {s['gallons_purchased']:.2f} gal | Cost: ${s['cost_usd']:.2f}")
            prev_mile = s["distance_along_route_miles"]
        final_leg = summary["total_distance_miles"] - prev_mile
        print(f"  Final Leg to Finish: {final_leg:.1f} miles (<= 500 mi: {final_leg <= 500.0})")

    # Second run to test caching
    t_cache_start = time.perf_counter()
    cache_response = client.post(
        "/api/v1/route/fuel-plan/",
        {"start": origin, "finish": destination},
        format="json",
    )
    t_cache = (time.perf_counter() - t_cache_start) * 1000
    cache_data = cache_response.json()
    print(f"\nCache Hit Verification: route_cached={cache_data['metadata']['route_cached']} (latency: {t_cache:.1f} ms)")


if __name__ == "__main__":
    evaluate_api_route("New York, NY", "Boston, MA")
    evaluate_api_route("New York, NY", "Chicago, IL")
    evaluate_api_route("Los Angeles, CA", "New York, NY")
