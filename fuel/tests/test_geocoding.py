from unittest.mock import Mock, patch

import requests
from django.core.cache import cache
from django.test import SimpleTestCase

from fuel.services.exceptions import LocationNotFoundError, UpstreamServiceError
from fuel.services.geocoding import geocode


def mock_response(json_data=None, status_ok=True, json_error=False):
    response = Mock()
    if json_error:
        response.json.side_effect = ValueError("invalid json")
    else:
        response.json.return_value = json_data
    if status_ok:
        response.raise_for_status.return_value = None
    else:
        response.raise_for_status.side_effect = requests.HTTPError("500 error")
    return response


class GeocodeTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch("fuel.services.geocoding.SESSION")
    def test_successful_geocode_returns_lat_lon(self, mock_session):
        mock_session.get.return_value = mock_response([{"lat": "41.88", "lon": "-87.62"}])

        result = geocode("Chicago, IL")

        self.assertEqual(result, (41.88, -87.62))
        mock_session.get.assert_called_once()

    @patch("fuel.services.geocoding.SESSION")
    def test_cache_hit_skips_the_http_call_entirely(self, mock_session):
        mock_session.get.return_value = mock_response([{"lat": "41.88", "lon": "-87.62"}])
        geocode("Chicago, IL")
        mock_session.get.reset_mock()

        result = geocode("Chicago, IL")

        self.assertEqual(result, (41.88, -87.62))
        mock_session.get.assert_not_called()

    @patch("fuel.services.geocoding.SESSION")
    def test_cache_key_is_normalized_across_case_and_whitespace(self, mock_session):
        mock_session.get.return_value = mock_response([{"lat": "41.88", "lon": "-87.62"}])
        geocode("  Chicago, IL  ")
        mock_session.get.reset_mock()

        geocode("chicago, il")

        mock_session.get.assert_not_called()

    @patch("fuel.services.geocoding.SESSION")
    def test_empty_results_raises_location_not_found(self, mock_session):
        mock_session.get.return_value = mock_response([])

        with self.assertRaises(LocationNotFoundError):
            geocode("nowhere at all xyz123")

    @patch("fuel.services.geocoding.SESSION")
    def test_request_exception_raises_upstream_service_error(self, mock_session):
        mock_session.get.side_effect = requests.ConnectionError("connection reset by peer")

        with self.assertRaises(UpstreamServiceError):
            geocode("Chicago, IL")

    @patch("fuel.services.geocoding.SESSION")
    def test_http_error_status_raises_upstream_service_error(self, mock_session):
        mock_session.get.return_value = mock_response(status_ok=False)

        with self.assertRaises(UpstreamServiceError):
            geocode("Chicago, IL")

    @patch("fuel.services.geocoding.SESSION")
    def test_malformed_json_raises_upstream_service_error(self, mock_session):
        mock_session.get.return_value = mock_response(json_error=True)

        with self.assertRaises(UpstreamServiceError):
            geocode("Chicago, IL")

    @patch("fuel.services.geocoding.SESSION")
    def test_a_failed_lookup_is_not_cached(self, mock_session):
        mock_session.get.side_effect = requests.ConnectionError("boom")
        with self.assertRaises(UpstreamServiceError):
            geocode("Chicago, IL")

        mock_session.get.side_effect = None
        mock_session.get.return_value = mock_response([{"lat": "41.88", "lon": "-87.62"}])

        # A prior failure must not have poisoned the cache with a permanent
        # miss -- the next call should still try the network and succeed.
        result = geocode("Chicago, IL")
        self.assertEqual(result, (41.88, -87.62))
