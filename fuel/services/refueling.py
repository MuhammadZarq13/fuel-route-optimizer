from dataclasses import dataclass

from django.conf import settings

from fuel.services.corridor import CandidateStop
from fuel.services.exceptions import InfeasibleRouteError

FLOAT_EPSILON = 1e-6


@dataclass(frozen=True)
class FuelStop:
    candidate: CandidateStop
    gallons_purchased: float
    cost: float


@dataclass(frozen=True)
class RefuelingPlan:
    stops: list[FuelStop]
    total_gallons_purchased: float
    total_cost: float


def plan_refueling(
    candidates: list[CandidateStop],
    total_distance_miles: float,
    range_miles: float | None = None,
    mpg: float | None = None,
) -> RefuelingPlan:
    """Choose which fuel stations to fill up at, and how much, to minimize
    total cost, given a vehicle with a limited range per tank.

    Assumptions (documented, since the assignment doesn't pin these down):
    - The vehicle starts the trip with a full tank, so the first
      `range_miles` of the route need no fuel stop.
    - Any fuel left in the tank on arrival isn't credited back — it's a
      sunk cost, same as topping up a personal car before a long drive.

    Algorithm: this is the classic "optimal refueling" problem. Its
    provably-optimal greedy strategy is: at every station, look ahead as
    far as a full tank would reach. If a cheaper station exists within
    that range, buy only enough fuel to reach it (never overpay for gas
    you didn't need to buy here). Otherwise, this station is the cheapest
    available in its own look-ahead window, so fill the tank completely.
    Buying the minimum necessary whenever a cheaper option is ahead, and
    maximizing purchase at local price minima, dominates every other
    feasible strategy — swapping any purchase for a more-expensive one
    elsewhere can only raise the total cost.
    """
    range_miles = range_miles or settings.VEHICLE_RANGE_MILES
    mpg = mpg or settings.VEHICLE_MPG
    gallons_per_mile = 1.0 / mpg

    stops_sorted = sorted(candidates, key=lambda c: c.distance_along_route_miles)
    _assert_feasible(stops_sorted, total_distance_miles, range_miles)

    fuel_stops: list[FuelStop] = []
    position = 0.0
    fuel_range_remaining = range_miles

    for i, stop in enumerate(stops_sorted):
        gap = stop.distance_along_route_miles - position
        fuel_range_remaining -= gap
        position = stop.distance_along_route_miles

        lookahead_limit = position + range_miles
        cheaper_ahead_position = None
        for other in stops_sorted[i + 1:]:
            if other.distance_along_route_miles > lookahead_limit + FLOAT_EPSILON:
                break
            if other.station.price_per_gallon < stop.station.price_per_gallon:
                cheaper_ahead_position = other.distance_along_route_miles
                break

        if cheaper_ahead_position is not None:
            target_position = min(cheaper_ahead_position, total_distance_miles)
        elif position + range_miles >= total_distance_miles - FLOAT_EPSILON:
            target_position = total_distance_miles
        else:
            target_position = position + range_miles

        needed_range = target_position - position
        top_up_range = max(0.0, needed_range - fuel_range_remaining)

        if top_up_range > FLOAT_EPSILON:
            gallons = top_up_range * gallons_per_mile
            cost = gallons * float(stop.station.price_per_gallon)
            fuel_stops.append(FuelStop(candidate=stop, gallons_purchased=gallons, cost=cost))
            fuel_range_remaining += top_up_range

    total_gallons = sum(s.gallons_purchased for s in fuel_stops)
    total_cost = sum(s.cost for s in fuel_stops)
    return RefuelingPlan(stops=fuel_stops, total_gallons_purchased=total_gallons, total_cost=total_cost)


def _assert_feasible(
    stops_sorted: list[CandidateStop], total_distance_miles: float, range_miles: float
) -> None:
    waypoints = [0.0] + [s.distance_along_route_miles for s in stops_sorted] + [total_distance_miles]
    for prev, curr in zip(waypoints, waypoints[1:]):
        gap = curr - prev
        if gap > range_miles + FLOAT_EPSILON:
            raise InfeasibleRouteError(
                f"No fuel station within vehicle range: a {gap:.1f} mile gap "
                f"exceeds the {range_miles:.0f} mile range between mile "
                f"{prev:.1f} and mile {curr:.1f} of the route."
            )
