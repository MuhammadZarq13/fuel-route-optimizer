from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from fuel.models import FuelStation
from fuel.services.exceptions import LocationNotFoundError
from fuel.services.routing import Route


class TripPlanAPITests(APITestCase):
    def setUp(self):
        self.url = reverse("trip-plan")
        # A short straight route along longitude -90 covering ~276 miles,
        # well within the 500-mile range, so no fuel stop should be needed.
        self.short_route = Route(
            coordinates=[(-90.0, lat / 10) for lat in range(400, 441)],
            distance_miles=276.0,
        )

    def _mock_geocode(self, start_finish_map):
        def side_effect(location):
            return start_finish_map[location]

        return side_effect

    @patch("fuel.views.get_route")
    @patch("fuel.views.geocode")
    def test_happy_path_returns_route_and_no_stops_needed(self, mock_geocode, mock_get_route):
        mock_geocode.side_effect = self._mock_geocode({"A": (40.0, -90.0), "B": (44.0, -90.0)})
        mock_get_route.return_value = self.short_route

        response = self.client.post(self.url, {"start": "A", "finish": "B"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["distance_miles"], 276.0)
        self.assertEqual(response.data["fuel_stops"], [])
        self.assertEqual(response.data["total_fuel_cost"], 0)
        self.assertEqual(mock_geocode.call_count, 2)
        self.assertEqual(mock_get_route.call_count, 1)

    @patch("fuel.views.get_route")
    @patch("fuel.views.geocode")
    def test_includes_cheapest_reachable_fuel_stop(self, mock_geocode, mock_get_route):
        mock_geocode.side_effect = self._mock_geocode({"A": (40.0, -90.0), "B": (44.0, -90.0)})
        mock_get_route.return_value = self.short_route
        FuelStation.objects.create(
            opis_id=1, name="Cheap Stop", address="addr", city="City", state="CA",
            rack_id=1, price_per_gallon=Decimal("2.50"), latitude=41.0, longitude=-90.0,
        )

        response = self.client.post(self.url, {"start": "A", "finish": "B"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Trip fits in the initial full tank, so this stop still isn't needed.
        self.assertEqual(response.data["fuel_stops"], [])

    def test_missing_field_returns_400(self):
        response = self.client.post(self.url, {"start": "A"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("fuel.views.geocode")
    def test_ungeocodable_location_returns_400(self, mock_geocode):
        mock_geocode.side_effect = LocationNotFoundError("nope")
        response = self.client.post(
            self.url, {"start": "nowhere", "finish": "B"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("fuel.views.get_route")
    @patch("fuel.views.geocode")
    def test_infeasible_route_returns_422(self, mock_geocode, mock_get_route):
        mock_geocode.side_effect = self._mock_geocode({"A": (40.0, -90.0), "B": (60.0, -90.0)})
        # ~1380 miles with zero fuel stations in range anywhere -> infeasible.
        mock_get_route.return_value = Route(
            coordinates=[(-90.0, 40.0), (-90.0, 60.0)], distance_miles=1380.0
        )
        response = self.client.post(self.url, {"start": "A", "finish": "B"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
