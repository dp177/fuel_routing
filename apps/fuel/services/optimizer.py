import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from apps.core.exceptions import NoFeasibleFuelPlanError

logger = logging.getLogger(__name__)

MPG = 10.0
TANK_CAPACITY_GALLONS = 50.0
MAX_RANGE_MILES = 500.0
STATION_POSITION_TOLERANCE_MILES = 0.5
EPSILON = 1e-4


@dataclass(frozen=True)
class FuelStop:
    """Represents a scheduled fuel stop with purchase and tank state details."""

    stop_number: int
    station_id: int
    name: str
    city: str
    state: str
    latitude: float
    longitude: float
    price_per_gallon: Decimal
    distance_along_route_miles: float
    distance_from_route_miles: float
    gallons_before_purchase: float
    gallons_purchased: float
    gallons_after_purchase: float
    fuel_consumed_to_next_stop: float
    cost_usd: Decimal
    address: str = ""
    coordinate_source: str = "city_centroid"

    def to_dict(self) -> dict:
        return {
            "stop_number": self.stop_number,
            "station_id": self.station_id,
            "name": self.name,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "latitude": round(self.latitude, 5),
            "longitude": round(self.longitude, 5),
            "price_per_gallon": float(self.price_per_gallon),
            "distance_along_route_miles": round(self.distance_along_route_miles, 2),
            "distance_from_route_miles": round(self.distance_from_route_miles, 2),
            "gallons_before_purchase": round(self.gallons_before_purchase, 2),
            "gallons_purchased": round(self.gallons_purchased, 2),
            "gallons_after_purchase": round(self.gallons_after_purchase, 2),
            "fuel_consumed_to_next_stop": round(self.fuel_consumed_to_next_stop, 2),
            "cost_usd": float(self.cost_usd),
            "coordinate_source": self.coordinate_source,
        }


@dataclass
class FuelPlanResult:
    """Full fuel optimization output containing stops and financial/physical summary."""

    fuel_stops: list[FuelStop]
    summary: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "fuel_stops": [s.to_dict() for s in self.fuel_stops],
            "summary": self.summary,
        }


class FuelOptimizer:
    """Fuel Stop Optimizer implementing greedy lookahead with cluster collapsing,

    strict physical reachability checks, and exact Decimal monetary accounting.
    """

    def __init__(
        self,
        mpg: float = MPG,
        tank_capacity_gallons: float = TANK_CAPACITY_GALLONS,
        cluster_tolerance_miles: float = STATION_POSITION_TOLERANCE_MILES,
    ):
        self.mpg = mpg
        self.tank_capacity = tank_capacity_gallons
        self.max_range = self.mpg * self.tank_capacity
        self.cluster_tolerance = cluster_tolerance_miles

    def cluster_stations(self, candidates: list[Any]) -> list[dict[str, Any]]:
        """Group candidate stations within cluster_tolerance_miles along the route

        and preserve the station offering the lowest price per gallon in each cluster.
        """
        if not candidates:
            return []

        # Normalize candidates to dicts
        cand_dicts = []
        for c in candidates:
            if hasattr(c, "to_dict"):
                cand_dicts.append(c.to_dict())
            elif isinstance(c, dict):
                cand_dicts.append(dict(c))
            else:
                cand_dicts.append(
                    {
                        "station_id": getattr(c, "station_id", 0),
                        "name": getattr(c, "name", ""),
                        "address": getattr(c, "address", ""),
                        "city": getattr(c, "city", ""),
                        "state": getattr(c, "state", ""),
                        "price_per_gallon": getattr(c, "price_per_gallon", Decimal("0.0")),
                        "latitude": getattr(c, "latitude", 0.0),
                        "longitude": getattr(c, "longitude", 0.0),
                        "distance_along_route_miles": getattr(c, "distance_along_route_miles", 0.0),
                        "distance_from_route_miles": getattr(c, "distance_from_route_miles", 0.0),
                        "coordinate_source": getattr(c, "coordinate_source", "city_centroid"),
                    }
                )

        sorted_cands = sorted(cand_dicts, key=lambda c: c["distance_along_route_miles"])

        clusters: list[list[dict[str, Any]]] = []
        current_cluster = [sorted_cands[0]]

        for c in sorted_cands[1:]:
            dist_diff = c["distance_along_route_miles"] - current_cluster[-1]["distance_along_route_miles"]
            if dist_diff <= self.cluster_tolerance:
                current_cluster.append(c)
            else:
                clusters.append(current_cluster)
                current_cluster = [c]
        clusters.append(current_cluster)

        result: list[dict[str, Any]] = []
        for cl in clusters:
            # Select the cheapest usable station in the cluster
            cheapest = min(
                cl,
                key=lambda x: (
                    Decimal(str(x["price_per_gallon"])),
                    x.get("distance_from_route_miles", 0.0),
                    x.get("station_id", 0),
                ),
            )
            result.append(cheapest)

        return result

    def check_feasibility(
        self,
        total_distance: float,
        stations: list[dict[str, Any]],
        initial_fuel: float = TANK_CAPACITY_GALLONS,
    ) -> bool:
        """Verify whether destination is physically reachable given a 500-mile tank limit."""
        curr_reach = initial_fuel * self.mpg
        if total_distance <= curr_reach + EPSILON:
            return True

        idx = 0
        n = len(stations)
        while curr_reach < total_distance - EPSILON:
            reachable = [
                i for i in range(idx, n) if stations[i]["distance_along_route_miles"] <= curr_reach + EPSILON
            ]
            if not reachable:
                return False
            best_idx = reachable[-1]
            if best_idx < idx:
                return False
            idx = best_idx + 1
            curr_reach = stations[best_idx]["distance_along_route_miles"] + self.max_range

        return True

    def optimize(
        self,
        total_distance_miles: float,
        candidate_stations: list[Any],
        initial_fuel: float = TANK_CAPACITY_GALLONS,
    ) -> FuelPlanResult:
        """Calculate the cost-effective fuel stops and purchase quantities along the route.

        Args:
            total_distance_miles: Total driving road distance in miles.
            candidate_stations: Candidate fuel stations along the route corridor.
            initial_fuel: Fuel level at trip origin in gallons (defaults to full tank).

        Returns:
            FuelPlanResult containing selected stops and summary.

        Raises:
            NoFeasibleFuelPlanError: If reachability gaps exceed vehicle range.
        """
        clustered = self.cluster_stations(candidate_stations)

        # 1. Impossibility gap detection
        if not self.check_feasibility(total_distance_miles, clustered, initial_fuel):
            logger.warning(
                "Feasibility check failed for route length %.2f mi with %d stations.",
                total_distance_miles,
                len(clustered),
            )
            raise NoFeasibleFuelPlanError(
                "No feasible sequence of fuel stops can cover the route within the vehicle's 500-mile range."
            )

        total_consumed = round(total_distance_miles / self.mpg, 2)

        # 2. Starting condition: Trip within initial tank range
        if total_distance_miles <= initial_fuel * self.mpg + EPSILON:
            summary = {
                "vehicle_mpg": self.mpg,
                "tank_capacity_gallons": self.tank_capacity,
                "max_range_miles": self.max_range,
                "vehicle_max_range_miles": self.max_range,
                "total_distance_miles": round(total_distance_miles, 2),
                "total_gallons_consumed": total_consumed,
                "total_gallons_purchased": 0.0,
                "total_cost_usd": 0.0,
                "total_stops": 0,
            }
            return FuelPlanResult(fuel_stops=[], summary=summary)

        # 3. Build virtual sequence: Start (idx 0), Stations (1..N), Destination (N+1)
        nodes: list[dict[str, Any]] = [
            {
                "dist": 0.0,
                "price": Decimal("999999.0"),  # Start fuel cannot be bought; price set infinite
                "station_id": 0,
                "name": "Start",
                "city": "",
                "state": "",
                "latitude": 0.0,
                "longitude": 0.0,
                "address": "",
                "distance_from_route_miles": 0.0,
                "coordinate_source": "origin",
            }
        ]

        for s in clustered:
            nodes.append(
                {
                    "dist": float(s["distance_along_route_miles"]),
                    "price": Decimal(str(s["price_per_gallon"])),
                    "station_id": s["station_id"],
                    "name": s.get("name", ""),
                    "city": s.get("city", ""),
                    "state": s.get("state", ""),
                    "latitude": float(s.get("latitude", 0.0)),
                    "longitude": float(s.get("longitude", 0.0)),
                    "address": s.get("address", ""),
                    "distance_from_route_miles": float(s.get("distance_from_route_miles", 0.0)),
                    "coordinate_source": s.get("coordinate_source", "city_centroid"),
                }
            )

        nodes.append(
            {
                "dist": float(total_distance_miles),
                "price": Decimal("0.0"),  # Virtual destination has no fuel purchase
                "station_id": -1,
                "name": "Destination",
                "city": "",
                "state": "",
                "latitude": 0.0,
                "longitude": 0.0,
                "address": "",
                "distance_from_route_miles": 0.0,
                "coordinate_source": "destination",
            }
        )

        n = len(nodes)
        curr_idx = 0
        curr_fuel = float(initial_fuel)

        raw_stops: list[dict[str, Any]] = []
        total_purchased_gal = Decimal("0.0")
        total_cost_dec = Decimal("0.00")

        # 4. Greedy lookahead forward simulation
        while curr_idx < n - 1:
            curr_dist = nodes[curr_idx]["dist"]
            curr_price = nodes[curr_idx]["price"]

            # If Destination is reachable with current fuel, stop looking
            dist_to_dest = total_distance_miles - curr_dist
            if dist_to_dest <= curr_fuel * self.mpg + EPSILON:
                break

            # Reachable horizon
            if curr_idx == 0:
                max_reach = curr_dist + curr_fuel * self.mpg
            else:
                max_reach = curr_dist + self.max_range

            reachable_indices = [
                i for i in range(curr_idx + 1, n) if nodes[i]["dist"] <= max_reach + EPSILON
            ]
            if not reachable_indices:
                raise NoFeasibleFuelPlanError(
                    "No feasible sequence of fuel stops can cover the route within the vehicle's 500-mile range."
                )

            # Look ahead for strictly cheaper stations
            cheaper_indices = [
                i for i in reachable_indices if nodes[i]["price"] < curr_price - Decimal("0.0001")
            ]

            if cheaper_indices:
                # Case A: Advance to the first cheaper station
                next_idx = cheaper_indices[0]
                dist_to_next = nodes[next_idx]["dist"] - curr_dist
                fuel_needed = dist_to_next / self.mpg

                if curr_fuel < fuel_needed - EPSILON:
                    buy_amount = fuel_needed - curr_fuel
                    buy_dec = Decimal(str(round(buy_amount, 6)))
                    cost = (buy_dec * curr_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                    if curr_idx > 0:
                        raw_stops.append(
                            {
                                "node": nodes[curr_idx],
                                "gallons_before": curr_fuel,
                                "gallons_purchased": buy_amount,
                                "gallons_after": fuel_needed,
                                "cost": cost,
                            }
                        )
                        total_purchased_gal += buy_dec
                        total_cost_dec += cost
                    curr_fuel = fuel_needed

                curr_fuel -= fuel_needed
                curr_idx = next_idx

            else:
                # Case B: No cheaper station within 500-mile reach
                # If destination is reachable with a full tank, purchase only what's needed
                if total_distance_miles <= max_reach + EPSILON:
                    fuel_needed = (total_distance_miles - curr_dist) / self.mpg
                    if curr_fuel < fuel_needed - EPSILON:
                        buy_amount = fuel_needed - curr_fuel
                        buy_dec = Decimal(str(round(buy_amount, 6)))
                        cost = (buy_dec * curr_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                        if curr_idx > 0:
                            raw_stops.append(
                                {
                                    "node": nodes[curr_idx],
                                    "gallons_before": curr_fuel,
                                    "gallons_purchased": buy_amount,
                                    "gallons_after": fuel_needed,
                                    "cost": cost,
                                }
                            )
                            total_purchased_gal += buy_dec
                            total_cost_dec += cost
                        curr_fuel = fuel_needed
                    break
                else:
                    # Fill tank to capacity (50 gallons) at this cheap station
                    if curr_fuel < self.tank_capacity - EPSILON:
                        buy_amount = self.tank_capacity - curr_fuel
                        buy_dec = Decimal(str(round(buy_amount, 6)))
                        cost = (buy_dec * curr_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                        if curr_idx > 0:
                            raw_stops.append(
                                {
                                    "node": nodes[curr_idx],
                                    "gallons_before": curr_fuel,
                                    "gallons_purchased": buy_amount,
                                    "gallons_after": self.tank_capacity,
                                    "cost": cost,
                                }
                            )
                            total_purchased_gal += buy_dec
                            total_cost_dec += cost
                        curr_fuel = self.tank_capacity

                    # Select best station to advance to (excluding destination since destination is not reachable)
                    cand_indices = [i for i in reachable_indices if i < n - 1]
                    if not cand_indices:
                        raise NoFeasibleFuelPlanError(
                            "No feasible sequence of fuel stops can cover the route within the vehicle's 500-mile range."
                        )

                    next_idx = min(cand_indices, key=lambda i: (nodes[i]["price"], -nodes[i]["dist"]))
                    dist_to_next = nodes[next_idx]["dist"] - curr_dist
                    curr_fuel -= dist_to_next / self.mpg
                    curr_idx = next_idx

        # 5. Format structured FuelStop objects with leg consumption
        formatted_stops: list[FuelStop] = []
        for idx, stop_data in enumerate(raw_stops, 1):
            node = stop_data["node"]
            current_dist = node["dist"]

            # Next stop position or Destination
            if idx < len(raw_stops):
                next_dist = raw_stops[idx]["node"]["dist"]
            else:
                next_dist = total_distance_miles

            leg_consumed = (next_dist - current_dist) / self.mpg

            formatted_stops.append(
                FuelStop(
                    stop_number=idx,
                    station_id=node["station_id"],
                    name=node["name"],
                    city=node["city"],
                    state=node["state"],
                    latitude=node["latitude"],
                    longitude=node["longitude"],
                    price_per_gallon=node["price"],
                    distance_along_route_miles=current_dist,
                    distance_from_route_miles=node["distance_from_route_miles"],
                    gallons_before_purchase=round(stop_data["gallons_before"], 2),
                    gallons_purchased=round(stop_data["gallons_purchased"], 2),
                    gallons_after_purchase=round(stop_data["gallons_after"], 2),
                    fuel_consumed_to_next_stop=round(leg_consumed, 2),
                    cost_usd=stop_data["cost"],
                    address=node["address"],
                    coordinate_source=node["coordinate_source"],
                )
            )

        summary = {
            "vehicle_mpg": self.mpg,
            "tank_capacity_gallons": self.tank_capacity,
            "max_range_miles": self.max_range,
            "vehicle_max_range_miles": self.max_range,
            "total_distance_miles": round(total_distance_miles, 2),
            "total_gallons_consumed": total_consumed,
            "total_gallons_purchased": round(float(total_purchased_gal), 2),
            "total_cost_usd": float(total_cost_dec),
            "total_stops": len(formatted_stops),
        }

        return FuelPlanResult(fuel_stops=formatted_stops, summary=summary)
