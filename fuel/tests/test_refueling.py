from decimal import Decimal

from django.test import SimpleTestCase

from fuel.models import FuelStation
from fuel.services.corridor import CandidateStop
from fuel.services.exceptions import InfeasibleRouteError
from fuel.services.refueling import plan_refueling


def make_candidate(position: float, price: float) -> CandidateStop:
    station = FuelStation(
        name=f"stop@{position}",
        address="addr",
        city="City",
        state="CA",
        opis_id=1,
        rack_id=1,
        price_per_gallon=Decimal(str(price)),
    )
    return CandidateStop(station=station, distance_along_route_miles=position, distance_from_route_miles=0.0)


class PlanRefuelingTests(SimpleTestCase):
    def test_no_stops_needed_within_range(self):
        candidates = [make_candidate(100, 4.0)]
        plan = plan_refueling(candidates, total_distance_miles=300, range_miles=500, mpg=10)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.total_cost, 0)

    def test_skips_expensive_station_to_reach_cheaper_one_in_range(self):
        candidates = [make_candidate(100, 4.0), make_candidate(300, 3.0)]
        plan = plan_refueling(candidates, total_distance_miles=700, range_miles=500, mpg=10)
        # 100 is skipped (0 fuel purchased there) because 300 is cheaper and reachable.
        self.assertEqual(len(plan.stops), 1)
        self.assertEqual(plan.stops[0].candidate.distance_along_route_miles, 300)

    def test_matches_hand_computed_multi_stop_plan(self):
        # Same scenario worked out by hand: buy 10gal@300(3.0)=$30, then
        # 40gal@600(2.5)=$100, skipping the pricier 100 and 450 stations.
        candidates = [
            make_candidate(100, 4.0),
            make_candidate(300, 3.0),
            make_candidate(450, 5.0),
            make_candidate(600, 2.5),
            make_candidate(900, 3.5),
        ]
        plan = plan_refueling(candidates, total_distance_miles=1000, range_miles=500, mpg=10)

        positions = [s.candidate.distance_along_route_miles for s in plan.stops]
        self.assertEqual(positions, [300, 600])
        self.assertAlmostEqual(plan.total_gallons_purchased, 50.0)
        self.assertAlmostEqual(plan.total_cost, 130.0)

    def test_tops_up_just_enough_to_reach_destination_when_already_cheapest(self):
        # Only station is expensive, but it's still the cheapest available
        # in its own lookahead window (destination is reachable from it),
        # so it should top up only what's needed to finish the trip.
        candidates = [make_candidate(100, 5.0)]
        plan = plan_refueling(candidates, total_distance_miles=550, range_miles=500, mpg=10)
        self.assertEqual(len(plan.stops), 1)
        # At mile 100 the tank still has 400 miles of range; only 50 more
        # miles of range (5 gallons) are needed to cover the remaining
        # 450 miles to the destination.
        self.assertAlmostEqual(plan.stops[0].gallons_purchased, 5.0)

    def test_fills_completely_when_no_cheaper_station_within_full_tank_range(self):
        # Neither the destination nor a cheaper station is within a full
        # tank's reach from this station, so it should top up to a
        # completely full tank rather than a partial amount.
        candidates = [make_candidate(100, 5.0), make_candidate(550, 6.0)]
        plan = plan_refueling(candidates, total_distance_miles=900, range_miles=500, mpg=10)
        # At mile 100 the tank has 400 miles of range left; topping up to a
        # full 500-mile tank needs 100 more miles of range (10 gallons).
        # (The second, pricier station still needs a top-up too, since it's
        # the only way to cover the remaining distance to the destination.)
        first_stop = plan.stops[0]
        self.assertEqual(first_stop.candidate.distance_along_route_miles, 100)
        self.assertAlmostEqual(first_stop.gallons_purchased, 10.0)

    def test_raises_when_gap_exceeds_range(self):
        candidates = [make_candidate(100, 4.0), make_candidate(700, 3.0)]
        with self.assertRaises(InfeasibleRouteError):
            plan_refueling(candidates, total_distance_miles=1000, range_miles=500, mpg=10)

    def test_raises_when_start_to_first_station_exceeds_range(self):
        candidates = [make_candidate(600, 3.0)]
        with self.assertRaises(InfeasibleRouteError):
            plan_refueling(candidates, total_distance_miles=1000, range_miles=500, mpg=10)
