import math
from dataclasses import dataclass

from fuel.models import FuelStation
from fuel.services.routing import Route

EARTH_RADIUS_MILES = 3958.8

# Fuel stations are only geocoded to city-center precision (see
# fuel/data/geocode_cache.json), not their exact highway-exit address, so
# the corridor has to be wide enough to absorb that imprecision.
CORRIDOR_WIDTH_MILES = 15

# Downsample the route polyline before doing per-station distance checks
# (OSRM can return thousands of points; we only need ~1 every couple of
# miles to place stations accurately along the route).
ROUTE_SAMPLE_INTERVAL_MILES = 2


@dataclass(frozen=True)
class RoutePoint:
    latitude: float
    longitude: float
    distance_along_route_miles: float


@dataclass(frozen=True)
class CandidateStop:
    station: FuelStation
    distance_along_route_miles: float
    distance_from_route_miles: float


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def build_route_points(route: Route) -> list[RoutePoint]:
    """Convert OSRM's (lon, lat) polyline into cumulative-distance-tagged
    points, downsampled so later per-station scans stay cheap."""
    points: list[RoutePoint] = []
    cumulative = 0.0
    prev_lat, prev_lon = None, None
    last_kept_at = 0.0

    for lon, lat in route.coordinates:
        if prev_lat is not None:
            cumulative += haversine_miles(prev_lat, prev_lon, lat, lon)
        prev_lat, prev_lon = lat, lon

        if not points or cumulative - last_kept_at >= ROUTE_SAMPLE_INTERVAL_MILES:
            points.append(RoutePoint(latitude=lat, longitude=lon, distance_along_route_miles=cumulative))
            last_kept_at = cumulative

    # Always keep the true endpoint, even if it's closer than the sample interval.
    if not points or points[-1].distance_along_route_miles != cumulative:
        points.append(RoutePoint(latitude=prev_lat, longitude=prev_lon, distance_along_route_miles=cumulative))

    return points


def _route_bounding_box(points: list[RoutePoint], buffer_miles: float) -> tuple[float, float, float, float]:
    lats = [p.latitude for p in points]
    lons = [p.longitude for p in points]
    avg_lat = sum(lats) / len(lats)

    lat_buffer = buffer_miles / 69.0
    lon_buffer = buffer_miles / (69.0 * max(math.cos(math.radians(avg_lat)), 0.1))

    return (
        min(lats) - lat_buffer,
        max(lats) + lat_buffer,
        min(lons) - lon_buffer,
        max(lons) + lon_buffer,
    )


def find_candidate_stops(route: Route) -> list[CandidateStop]:
    """Find fuel stations near the route, each tagged with how far along
    the route it sits. Uses a DB-level bounding-box prefilter (index-backed
    on latitude/longitude) before the precise Haversine check, so this
    stays fast even with thousands of stations in the table."""
    route_points = build_route_points(route)
    min_lat, max_lat, min_lon, max_lon = _route_bounding_box(route_points, CORRIDOR_WIDTH_MILES)

    nearby_stations = FuelStation.objects.filter(
        latitude__isnull=False,
        longitude__isnull=False,
        latitude__gte=min_lat,
        latitude__lte=max_lat,
        longitude__gte=min_lon,
        longitude__lte=max_lon,
    )

    candidates: list[CandidateStop] = []
    for station in nearby_stations:
        best_distance = math.inf
        best_along = 0.0
        for point in route_points:
            d = haversine_miles(station.latitude, station.longitude, point.latitude, point.longitude)
            if d < best_distance:
                best_distance = d
                best_along = point.distance_along_route_miles

        if best_distance <= CORRIDOR_WIDTH_MILES:
            candidates.append(
                CandidateStop(
                    station=station,
                    distance_along_route_miles=best_along,
                    distance_from_route_miles=best_distance,
                )
            )

    candidates.sort(key=lambda c: c.distance_along_route_miles)
    return candidates
