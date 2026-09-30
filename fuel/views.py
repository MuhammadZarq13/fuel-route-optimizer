from concurrent.futures import ThreadPoolExecutor

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from fuel.serializers import TripPlanRequestSerializer, TripPlanResponseSerializer
from fuel.services.corridor import build_route_points, find_candidate_stops
from fuel.services.exceptions import (
    InfeasibleRouteError,
    LocationNotFoundError,
    RouteNotFoundError,
    UpstreamServiceError,
)
from fuel.services.geocoding import geocode
from fuel.services.refueling import plan_refueling
from fuel.services.routing import get_route


class TripPlanView(APIView):
    """POST {"start": "...", "finish": "..."} -> route + optimal fuel stops + total cost.

    Makes exactly 3 external HTTP calls per request in the worst case
    (geocode start, geocode finish, one OSRM route call) — fewer if the
    geocode/route results are already cached from a prior identical
    request. The two geocode calls are independent, so they run
    concurrently on a 2-worker thread pool rather than back-to-back.
    """

    def get_serializer(self, *args, **kwargs):
        # Lets DRF's browsable API render a proper start/finish form
        # instead of a raw JSON textarea when viewed in a browser.
        return TripPlanRequestSerializer(*args, **kwargs)

    def post(self, request):
        request_serializer = TripPlanRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        start_query = request_serializer.validated_data["start"]
        finish_query = request_serializer.validated_data["finish"]

        try:
            # `with` guarantees executor.shutdown(wait=True) runs on the
            # way out (success, exception, anything) -- both worker
            # threads are always joined here before this block exits, so
            # nothing outlives the request.
            with ThreadPoolExecutor(max_workers=2) as executor:
                start_future = executor.submit(geocode, start_query)
                finish_future = executor.submit(geocode, finish_query)
                start_coords = start_future.result()
                finish_coords = finish_future.result()

            route = get_route(start_coords, finish_coords)
            route_points = build_route_points(route)
            candidates = find_candidate_stops(route_points)
            plan = plan_refueling(candidates, route.distance_miles)
        except LocationNotFoundError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except (RouteNotFoundError, InfeasibleRouteError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except UpstreamServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        payload = {
            "distance_miles": round(route.distance_miles, 1),
            # Downsampled to ~1 point per 2 miles (build_route_points), not
            # OSRM's raw polyline -- that can be tens of thousands of
            # points on a cross-country route, which is far more detail
            # than a map needs and makes the response needlessly large.
            "route": {
                "type": "LineString",
                "coordinates": [[p.longitude, p.latitude] for p in route_points],
            },
            "fuel_stops": [
                {
                    "name": s.candidate.station.name,
                    "address": s.candidate.station.address,
                    "city": s.candidate.station.city,
                    "state": s.candidate.station.state,
                    "latitude": s.candidate.station.latitude,
                    "longitude": s.candidate.station.longitude,
                    "price_per_gallon": s.candidate.station.price_per_gallon,
                    "distance_along_route_miles": round(s.candidate.distance_along_route_miles, 1),
                    "gallons_purchased": round(s.gallons_purchased, 2),
                    "cost": round(s.cost, 2),
                }
                for s in plan.stops
            ],
            "total_gallons_purchased": round(plan.total_gallons_purchased, 2),
            "total_fuel_cost": round(plan.total_cost, 2),
        }

        response_serializer = TripPlanResponseSerializer(payload)
        return Response(response_serializer.data, status=status.HTTP_200_OK)
