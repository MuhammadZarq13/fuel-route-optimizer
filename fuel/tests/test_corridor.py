from decimal import Decimal

from django.test import TestCase

from fuel.models import FuelStation
from fuel.services.corridor import (
    CORRIDOR_WIDTH_MILES,
    build_route_points,
    find_candidate_stops,
    haversine_miles,
)
from fuel.services.routing import Route


def make_station(name, lat, lon, price="3.00"):
    return FuelStation.objects.create(
        opis_id=1,
        name=name,
        address="addr",
        city="City",
        state="CA",
        rack_id=1,
        price_per_gallon=Decimal(price),
        latitude=lat,
        longitude=lon,
    )


def lon_offset_for_miles(miles: float, at_lat: float) -> float:
    """Reverse-derive a longitude delta that is approximately `miles` away
    from (at_lat, 0), by bisecting against the module's own haversine_miles
    so the test doesn't depend on a separately hand-rolled conversion."""
    lo, hi = 0.0, 5.0
    for _ in range(40):
        mid = (lo + hi) / 2
        d = haversine_miles(at_lat, 0.0, at_lat, mid)
        if d < miles:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


class FindCandidateStopsTests(TestCase):
    def setUp(self):
        # A straight north-south route along longitude -90, from lat 40 to lat 44
        # (~276 miles), which keeps mile-along-route math simple to reason about.
        self.route = Route(
            coordinates=[(-90.0, lat / 10) for lat in range(400, 441)],
            distance_miles=None,  # unused by build_route_points/find_candidate_stops
        )
        self.route_points = build_route_points(self.route)
        self.on_route_lat = 41.0

    def test_station_on_route_is_included_with_near_zero_offset(self):
        on_route = make_station("OnRoute", self.on_route_lat, -90.0)
        candidates = find_candidate_stops(self.route_points)
        stations = {c.station.id: c for c in candidates}
        self.assertIn(on_route.id, stations)
        self.assertLess(stations[on_route.id].distance_from_route_miles, 0.1)

    def test_station_just_inside_corridor_is_included(self):
        delta = lon_offset_for_miles(CORRIDOR_WIDTH_MILES - 2, self.on_route_lat)
        near = make_station("NearRoute", self.on_route_lat, -90.0 + delta)
        candidates = find_candidate_stops(self.route_points)
        self.assertIn(near.id, {c.station.id for c in candidates})

    def test_station_outside_corridor_is_excluded(self):
        delta = lon_offset_for_miles(CORRIDOR_WIDTH_MILES + 20, self.on_route_lat)
        far = make_station("FarFromRoute", self.on_route_lat, -90.0 + delta)
        candidates = find_candidate_stops(self.route_points)
        self.assertNotIn(far.id, {c.station.id for c in candidates})

    def test_stations_without_coordinates_are_ignored(self):
        FuelStation.objects.create(
            opis_id=2, name="NoCoords", address="a", city="c", state="CA",
            rack_id=1, price_per_gallon=Decimal("3.00"), latitude=None, longitude=None,
        )
        candidates = find_candidate_stops(self.route_points)
        self.assertTrue(all(c.station.name != "NoCoords" for c in candidates))

    def test_candidates_are_sorted_by_distance_along_route(self):
        make_station("A", 43.5, -90.0)
        make_station("B", 40.5, -90.0)
        candidates = find_candidate_stops(self.route_points)
        distances = [c.distance_along_route_miles for c in candidates]
        self.assertEqual(distances, sorted(distances))
