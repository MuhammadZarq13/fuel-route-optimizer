from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from fuel.services.exceptions import RouteNotFoundError, UpstreamServiceError
from fuel.services.http import SESSION

REQUEST_TIMEOUT_SECONDS = 15
METERS_PER_MILE = 1609.344


@dataclass(frozen=True)
class Route:
    # (longitude, latitude) pairs in route order, matching GeoJSON convention.
    coordinates: list[tuple[float, float]]
    distance_miles: float


def get_route(start: tuple[float, float], finish: tuple[float, float]) -> Route:
    """Fetch the driving route between two (lat, lon) points via OSRM.

    Exactly one HTTP call. Result is cached by rounded coordinates so a
    repeated trip request doesn't call OSRM again.
    """
    start_lat, start_lon = start
    finish_lat, finish_lon = finish

    cache_key = (
        f"route:{start_lat:.4f},{start_lon:.4f}:{finish_lat:.4f},{finish_lon:.4f}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return Route(coordinates=cached["coordinates"], distance_miles=cached["distance_miles"])

    url = (
        f"{settings.OSRM_BASE_URL}/route/v1/driving/"
        f"{start_lon},{start_lat};{finish_lon},{finish_lat}"
    )
    params = {"overview": "full", "geometries": "geojson", "steps": "false"}

    try:
        response = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise UpstreamServiceError(f"Routing request failed: {exc}") from exc

    if data.get("code") != "Ok" or not data.get("routes"):
        raise RouteNotFoundError(f"No route found between {start} and {finish}")

    route = data["routes"][0]
    coordinates = [tuple(point) for point in route["geometry"]["coordinates"]]
    distance_miles = route["distance"] / METERS_PER_MILE

    cache.set(
        cache_key,
        {"coordinates": coordinates, "distance_miles": distance_miles},
        settings.ROUTE_CACHE_TTL_SECONDS,
    )
    return Route(coordinates=coordinates, distance_miles=distance_miles)
