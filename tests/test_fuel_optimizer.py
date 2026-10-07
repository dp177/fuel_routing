import heapq
import random
from decimal import Decimal
import pytest

from apps.core.exceptions import NoFeasibleFuelPlanError
from apps.fuel.services.optimizer import FuelOptimizer, FuelStop


def solve_dijkstra_reference(
    total_distance: float,
    stations: list[dict],
    initial_fuel: float = 50.0,
    step: float = 0.5,
) -> float | None:
    """Independent reference solver using discretized state-space Dijkstra.

    Used strictly for algorithm validation against the primary FuelOptimizer.
    """
    nodes = [(0.0, 0.0)] + [(float(s["distance_along_route_miles"]), float(s["price_per_gallon"])) for s in stations] + [(float(total_distance), 0.0)]
    n = len(nodes)
    max_f = 50.0
    dist = {}
    init_state = (0, round(initial_fuel, 2))
    dist[init_state] = 0.0
    pq = [(0.0, 0, initial_fuel)]

    while pq:
        cost, u, f = heapq.heappop(pq)
        f = round(f, 2)
        if cost > dist.get((u, f), float("inf")) + 1e-6:
            continue
        if u == n - 1:
            return cost
        pos_u, price_u = nodes[u]
        buy_options = [0.0] if u == 0 else [g * step for g in range(int(round((max_f - f) / step)) + 1)]
        for g in buy_options:
            f_after = f + g
            cost_after = cost + (g * price_u if u > 0 else 0.0)
            for v in range(u + 1, n):
                pos_v, price_v = nodes[v]
                dist_uv = pos_v - pos_u
                fuel_needed = dist_uv / 10.0
                if fuel_needed <= f_after + 1e-6:
                    rem_f = round(f_after - fuel_needed, 2)
                    state_v = (v, rem_f)
                    if cost_after < dist.get(state_v, float("inf")) - 1e-6:
                        dist[state_v] = cost_after
                        heapq.heappush(pq, (cost_after, v, rem_f))
                else:
                    break
    return None


class TestFuelOptimizer:
    """Comprehensive test suite covering Requirements TEST 1 through TEST 12 and validation."""

    @pytest.fixture
    def optimizer(self):
        return FuelOptimizer(mpg=10.0, tank_capacity_gallons=50.0, cluster_tolerance_miles=0.5)

    def test_1_short_trip(self, optimizer):
        """TEST 1 — Short trip of 200 miles requires 0 fuel stops and $0 cost."""
        plan = optimizer.optimize(total_distance_miles=200.0, candidate_stations=[])
        assert len(plan.fuel_stops) == 0
        assert plan.summary["total_stops"] == 0
        assert plan.summary["total_gallons_consumed"] == 20.0
        assert plan.summary["total_gallons_purchased"] == 0.0
        assert plan.summary["total_cost_usd"] == 0.0

    def test_2_exactly_500_miles(self, optimizer):
        """TEST 2 — Exactly 500 miles trip reaches destination on initial tank with 0 stops."""
        plan = optimizer.optimize(total_distance_miles=500.0, candidate_stations=[])
        assert len(plan.fuel_stops) == 0
        assert plan.summary["total_stops"] == 0
        assert plan.summary["total_gallons_consumed"] == 50.0
        assert plan.summary["total_gallons_purchased"] == 0.0
        assert plan.summary["total_cost_usd"] == 0.0

    def test_3_trip_600_miles_single_stop(self, optimizer):
        """TEST 3 — 600 miles trip with Station A at 400 miles ($3.50/gal).

        Starting 50 gal. At A: 10 gal remain. Need 20 gal for remaining 200 mi.
        Purchase = 10 gal at $3.50 = $35.00.
        """
        stations = [
            {
                "station_id": 101,
                "name": "Station A",
                "city": "TestCity",
                "state": "OH",
                "distance_along_route_miles": 400.0,
                "price_per_gallon": Decimal("3.50"),
            }
        ]
        plan = optimizer.optimize(total_distance_miles=600.0, candidate_stations=stations)
        assert len(plan.fuel_stops) == 1

        stop = plan.fuel_stops[0]
        assert stop.station_id == 101
        assert stop.distance_along_route_miles == 400.0
        assert stop.gallons_before_purchase == 10.0
        assert stop.gallons_purchased == 10.0
        assert stop.gallons_after_purchase == 20.0
        assert stop.fuel_consumed_to_next_stop == 20.0  # 200 mi to finish / 10 mpg
        assert stop.cost_usd == Decimal("35.00")
        assert plan.summary["total_cost_usd"] == 35.00
        assert plan.summary["total_gallons_purchased"] == 10.0

    def test_4_cheap_station_reachable_avoids_expensive(self, optimizer):
        """TEST 4 — Cheap station B ($3.00 at 200 mi) reachable past expensive station A ($4.00 at 100 mi).

        Total distance 600 mi.
        Vehicle should NOT purchase expensive fuel at A when B is reachable.
        At B, purchases 10 gal to finish the remaining 400 mi. Cost = $30.00.
        """
        stations = [
            {
                "station_id": 1,
                "name": "Station A (Expensive)",
                "city": "CityA",
                "state": "PA",
                "distance_along_route_miles": 100.0,
                "price_per_gallon": Decimal("4.00"),
            },
            {
                "station_id": 2,
                "name": "Station B (Cheap)",
                "city": "CityB",
                "state": "OH",
                "distance_along_route_miles": 200.0,
                "price_per_gallon": Decimal("3.00"),
            },
        ]
        plan = optimizer.optimize(total_distance_miles=600.0, candidate_stations=stations)
        assert len(plan.fuel_stops) == 1
        assert plan.fuel_stops[0].station_id == 2
        assert plan.fuel_stops[0].gallons_purchased == 10.0
        assert plan.fuel_stops[0].cost_usd == Decimal("30.00")

    def test_5_cheap_station_unreachable(self, optimizer):
        """TEST 5 — Cheap station B is unreachable from A (gap = 550 miles > 500 max range).

        Optimizer must NOT select B and must report infeasibility.
        """
        stations = [
            {
                "station_id": 1,
                "name": "Station A",
                "city": "CityA",
                "state": "PA",
                "distance_along_route_miles": 100.0,
                "price_per_gallon": Decimal("3.00"),
            },
            {
                "station_id": 2,
                "name": "Station B",
                "city": "CityB",
                "state": "IL",
                "distance_along_route_miles": 650.0,  # 550 miles from A!
                "price_per_gallon": Decimal("2.00"),
            },
        ]
        with pytest.raises(NoFeasibleFuelPlanError):
            optimizer.optimize(total_distance_miles=750.0, candidate_stations=stations)

    def test_6_expensive_station_required_when_only_viable_path(self, optimizer):
        """TEST 6 — Only feasible continuation requires stopping at expensive station A ($4.50).

        Start -> 450 mi -> A ($4.50) -> 450 mi -> B ($3.00) -> 300 mi -> Finish (1200 mi).
        At A, buys only what is needed to reach B (40 gal).
        At B, buys 30 gal to reach finish.
        Total cost = 40*4.50 + 30*3.00 = 180 + 90 = $270.00.
        """
        stations = [
            {
                "station_id": 1,
                "name": "Expensive Required A",
                "city": "CityA",
                "state": "IN",
                "distance_along_route_miles": 450.0,
                "price_per_gallon": Decimal("4.50"),
            },
            {
                "station_id": 2,
                "name": "Cheap B",
                "city": "CityB",
                "state": "IL",
                "distance_along_route_miles": 900.0,
                "price_per_gallon": Decimal("3.00"),
            },
        ]
        plan = optimizer.optimize(total_distance_miles=1200.0, candidate_stations=stations)
        assert len(plan.fuel_stops) == 2
        assert plan.fuel_stops[0].station_id == 1
        assert plan.fuel_stops[0].gallons_purchased == 40.0
        assert plan.fuel_stops[0].cost_usd == Decimal("180.00")

        assert plan.fuel_stops[1].station_id == 2
        assert plan.fuel_stops[1].gallons_purchased == 30.0
        assert plan.fuel_stops[1].cost_usd == Decimal("90.00")
        assert plan.summary["total_cost_usd"] == 270.00

    def test_7_multiple_stops_long_route(self, optimizer):
        """TEST 7 — Route of 1400 miles with multiple stops.

        Verifies all legs <= 500 miles, tank never negative, tank never > 50, destination reached.
        """
        stations = [
            {"station_id": 1, "name": "Stop 1", "city": "C1", "state": "OH", "distance_along_route_miles": 400.0, "price_per_gallon": Decimal("3.40")},
            {"station_id": 2, "name": "Stop 2", "city": "C2", "state": "IN", "distance_along_route_miles": 800.0, "price_per_gallon": Decimal("3.20")},
            {"station_id": 3, "name": "Stop 3", "city": "C3", "state": "IL", "distance_along_route_miles": 1200.0, "price_per_gallon": Decimal("3.50")},
        ]
        plan = optimizer.optimize(total_distance_miles=1400.0, candidate_stations=stations)
        assert len(plan.fuel_stops) >= 2

        # Verify physical constraints on all stops
        for stop in plan.fuel_stops:
            assert 0.0 <= stop.gallons_before_purchase <= 50.0
            assert 0.0 <= stop.gallons_after_purchase <= 50.0
            assert stop.fuel_consumed_to_next_stop <= 50.0

        # Destination was reached
        assert plan.summary["total_distance_miles"] == 1400.0

    def test_8_no_feasible_plan_detected(self, optimizer):
        """TEST 8 — Gap exceeding 500 miles raises NoFeasibleFuelPlanError."""
        # 520 miles from origin to first station
        stations = [
            {"station_id": 1, "name": "Station 1", "city": "C1", "state": "NE", "distance_along_route_miles": 520.0, "price_per_gallon": Decimal("3.20")},
        ]
        with pytest.raises(NoFeasibleFuelPlanError):
            optimizer.optimize(total_distance_miles=800.0, candidate_stations=stations)

    def test_9_same_mileage_station_cluster(self, optimizer):
        """TEST 9 — Three stations at virtually identical mileage:

        A ($3.50 at 300.0), B ($3.20 at 300.1), C ($3.80 at 300.2).
        Optimizer must select the cheapest usable station (B at $3.20).
        """
        stations = [
            {"station_id": 10, "name": "A", "city": "Town", "state": "PA", "distance_along_route_miles": 300.0, "price_per_gallon": Decimal("3.50")},
            {"station_id": 20, "name": "B (Cheapest)", "city": "Town", "state": "PA", "distance_along_route_miles": 300.1, "price_per_gallon": Decimal("3.20")},
            {"station_id": 30, "name": "C", "city": "Town", "state": "PA", "distance_along_route_miles": 300.2, "price_per_gallon": Decimal("3.80")},
        ]
        plan = optimizer.optimize(total_distance_miles=600.0, candidate_stations=stations)
        assert len(plan.fuel_stops) == 1
        assert plan.fuel_stops[0].station_id == 20
        assert plan.fuel_stops[0].price_per_gallon == Decimal("3.20")

    def test_10_destination_reachable_buys_only_needed_fuel(self, optimizer):
        """TEST 10 — After stop at 400 miles, finish is at 600 miles (20 gal needed).

        Vehicle arrives with 10 gal. Optimizer must purchase exactly 10 gal, not 50 gal.
        """
        stations = [
            {"station_id": 1, "name": "S", "city": "C", "state": "OH", "distance_along_route_miles": 400.0, "price_per_gallon": Decimal("3.00")}
        ]
        plan = optimizer.optimize(total_distance_miles=600.0, candidate_stations=stations)
        assert plan.fuel_stops[0].gallons_purchased == 10.0
        assert plan.fuel_stops[0].gallons_after_purchase == 20.0  # Exactly 20, not 50!

    def test_11_exact_boundaries(self, optimizer):
        """TEST 11 — Boundaries: 499.999 (0 stops), 500.000 (0 stops), 500.001 without station (infeasible)."""
        # 499.999 miles
        p1 = optimizer.optimize(total_distance_miles=499.999, candidate_stations=[])
        assert len(p1.fuel_stops) == 0

        # 500.000 miles
        p2 = optimizer.optimize(total_distance_miles=500.000, candidate_stations=[])
        assert len(p2.fuel_stops) == 0

        # 500.001 miles without any station is physically infeasible
        with pytest.raises(NoFeasibleFuelPlanError):
            optimizer.optimize(total_distance_miles=500.001, candidate_stations=[])

    def test_12_exact_cost_decimal_precision(self, optimizer):
        """TEST 12 — Verify gallons * price = cost using exact Decimal arithmetic."""
        stations = [
            {
                "station_id": 1,
                "name": "S",
                "city": "C",
                "state": "OH",
                "distance_along_route_miles": 400.0,
                "price_per_gallon": Decimal("3.3333"),
            }
        ]
        plan = optimizer.optimize(total_distance_miles=600.0, candidate_stations=stations)
        stop = plan.fuel_stops[0]
        # 10.0 gal * 3.3333 = 33.33
        expected = (Decimal("10.0") * Decimal("3.3333")).quantize(Decimal("0.01"))
        assert stop.cost_usd == expected
        assert stop.cost_usd == Decimal("33.33")

    def test_algorithm_validation_vs_reference_solver(self, optimizer):
        """Requirement 11: Validates optimizer output against independent Dijkstra reference

        solver on 20 randomly generated synthetic feasible routes (5-7 stations).
        """
        random.seed(99)
        for _ in range(20):
            d = random.randint(600, 1200)
            pos = 0.0
            stations = []
            sid = 1
            while pos + 350 < d:
                pos += random.randint(150, 350)
                if pos < d - 50:
                    price = round(random.uniform(2.50, 4.50), 2)
                    stations.append(
                        {
                            "station_id": sid,
                            "name": f"Station {sid}",
                            "city": "City",
                            "state": "ST",
                            "distance_along_route_miles": float(pos),
                            "price_per_gallon": Decimal(str(price)),
                        }
                    )
                    sid += 1

            if not optimizer.check_feasibility(float(d), stations):
                continue

            opt_plan = optimizer.optimize(float(d), stations)
            ref_cost = solve_dijkstra_reference(float(d), stations, initial_fuel=50.0, step=0.5)

            if ref_cost is not None:
                # Difference should be within discrete step rounding
                assert abs(opt_plan.summary["total_cost_usd"] - ref_cost) <= 2.5
