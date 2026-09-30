from unittest.mock import Mock, patch

import requests
from django.core.cache import cache
from django.test import SimpleTestCase

from fuel.services.exceptions import RouteNotFoundError, UpstreamServiceError
from fuel.services.routing import METERS_PER_MILE, get_route


def mock_osrm_response(code="Ok", distance_meters=160934.4, coords=None, status_ok=True):
    coords = coords if coords is not None else [[-87.62, 41.88], [-86.0, 40.0]]
    response = Mock()
    response.raise_for_status.return_value = None if status_ok else None
    if not status_ok:
        response.raise_for_status.side_effect = requests.HTTPError("500 error")
    response.json.return_value = {
        "code": code,
        "routes": [{"geometry": {"coordinates": coords}, "distance": distance_meters}] if code == "Ok" else [],
    }
    return response


class GetRouteTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch("fuel.services.routing.SESSION")
    def test_successful_route_converts_meters_to_miles(self, mock_session):
        mock_session.get.return_value = mock_osrm_response(distance_meters=METERS_PER_MILE * 100)

        route = get_route((41.88, -87.62), (40.0, -86.0))

        self.assertAlmostEqual(route.distance_miles, 100.0)
        self.assertEqual(route.coordinates, [(-87.62, 41.88), (-86.0, 40.0)])
        mock_session.get.assert_called_once()

    @patch("fuel.services.routing.SESSION")
    def test_makes_exactly_one_call(self, mock_session):
        mock_session.get.return_value = mock_osrm_response()
        get_route((41.88, -87.62), (40.0, -86.0))
        self.assertEqual(mock_session.get.call_count, 1)

    @patch("fuel.services.routing.SESSION")
    def test_cache_hit_skips_the_http_call_entirely(self, mock_session):
        mock_session.get.return_value = mock_osrm_response()
        get_route((41.88, -87.62), (40.0, -86.0))
        mock_session.get.reset_mock()

        get_route((41.88, -87.62), (40.0, -86.0))

        mock_session.get.assert_not_called()

    @patch("fuel.services.routing.SESSION")
    def test_coordinates_rounded_to_same_cache_key_hit_cache(self, mock_session):
        mock_session.get.return_value = mock_osrm_response()
        get_route((41.88001, -87.62001), (40.0, -86.0))
        mock_session.get.reset_mock()

        # Differs only past the 4th decimal (~11m) -- same cache key.
        get_route((41.880011, -87.620011), (40.0, -86.0))

        mock_session.get.assert_not_called()

    @patch("fuel.services.routing.SESSION")
    def test_non_ok_code_raises_route_not_found(self, mock_session):
        mock_session.get.return_value = mock_osrm_response(code="NoRoute")

        with self.assertRaises(RouteNotFoundError):
            get_route((41.88, -87.62), (40.0, -86.0))

    @patch("fuel.services.routing.SESSION")
    def test_request_exception_raises_upstream_service_error(self, mock_session):
        mock_session.get.side_effect = requests.Timeout("timed out")

        with self.assertRaises(UpstreamServiceError):
            get_route((41.88, -87.62), (40.0, -86.0))

    @patch("fuel.services.routing.SESSION")
    def test_http_error_status_raises_upstream_service_error(self, mock_session):
        mock_session.get.return_value = mock_osrm_response(status_ok=False)

        with self.assertRaises(UpstreamServiceError):
            get_route((41.88, -87.62), (40.0, -86.0))

    @patch("fuel.services.routing.SESSION")
    def test_failed_route_is_not_cached(self, mock_session):
        mock_session.get.side_effect = requests.Timeout("timed out")
        with self.assertRaises(UpstreamServiceError):
            get_route((41.88, -87.62), (40.0, -86.0))

        mock_session.get.side_effect = None
        mock_session.get.return_value = mock_osrm_response()

        route = get_route((41.88, -87.62), (40.0, -86.0))
        self.assertIsNotNone(route)
