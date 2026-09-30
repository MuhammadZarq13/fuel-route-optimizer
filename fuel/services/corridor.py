import math
from dataclasses import dataclass

from django.db.models import Q

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

# A single bounding box around an entire route is tight for a route that's
# roughly north-south or east-west, but for a diagonal transcontinental
# route it barely filters anything -- measured on a real LA->NYC request,
# a whole-route bbox matched 52% of every geocoded station in the country
# before the precise per-station check even started. Chunking the route
# into segments and using the (much tighter) per-segment bbox, OR'd
# together into one query, fixes this without needing PostGIS: candidate
# count now scales with route length and station density near the route,
# not with how diagonal the route is or how large the station table is.
ROUTE_SEGMENT_LENGTH_MILES = 100


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


def _bbox_for_points(points: list[RoutePoint], buffer_miles: float) -> tuple[float, float, float, float]:
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


def _bbox_contains(bbox: tuple[float, float, float, float], lat: float, lon: float) -> bool:
    min_lat, max_lat, min_lon, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def _segment_route_points(
    points: list[RoutePoint], segment_length_miles: float
) -> list[list[RoutePoint]]:
    """Split route points into contiguous ~segment_length_miles chunks.
    Each new segment starts from the previous segment's last point, so
    nothing falls through the gap at a segment boundary."""
    if not points:
        return []

    segments: list[list[RoutePoint]] = [[points[0]]]
    segment_start = points[0].distance_along_route_miles
    for point in points[1:]:
        segments[-1].append(point)
        if point.distance_along_route_miles - segment_start >= segment_length_miles:
            segment_start = point.distance_along_route_miles
            segments.append([point])
    return segments


def find_candidate_stops(route_points: list[RoutePoint]) -> list[CandidateStop]:
    """Find fuel stations near the route, each tagged with how far along
    the route it sits.

    The route is chunked into ~100-mile segments, each with its own tight
    bounding box; the DB query is those boxes OR'd together (one query),
    and the later exact-distance pass for each candidate station only
    scans the segment(s) whose box it actually falls in -- not every
    point on the whole route. Both the DB result size and the per-station
    scan cost now track route length and station density near the route,
    rather than how diagonal the route is or how big the station table is.

    Takes pre-built route_points (see build_route_points) rather than a
    Route, so a caller that also needs the downsampled points for its own
    purposes (e.g. a lean response payload) doesn't pay to compute them
    twice."""
    segments = _segment_route_points(route_points, ROUTE_SEGMENT_LENGTH_MILES)
    segment_bboxes = [_bbox_for_points(segment, CORRIDOR_WIDTH_MILES) for segment in segments]

    combined_filter = Q(
        latitude__gte=segment_bboxes[0][0],
        latitude__lte=segment_bboxes[0][1],
        longitude__gte=segment_bboxes[0][2],
        longitude__lte=segment_bboxes[0][3],
    )
    for min_lat, max_lat, min_lon, max_lon in segment_bboxes[1:]:
        combined_filter |= Q(
            latitude__gte=min_lat, latitude__lte=max_lat, longitude__gte=min_lon, longitude__lte=max_lon
        )

    nearby_stations = FuelStation.objects.filter(
        combined_filter, latitude__isnull=False, longitude__isnull=False
    )

    candidates: list[CandidateStop] = []
    for station in nearby_stations:
        relevant_points = [
            point
            for segment, bbox in zip(segments, segment_bboxes)
            if _bbox_contains(bbox, station.latitude, station.longitude)
            for point in segment
        ]

        best_distance = math.inf
        best_along = 0.0
        for point in relevant_points:
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
