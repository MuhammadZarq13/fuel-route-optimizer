from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from fuel.serializers import TripPlanRequestSerializer, TripPlanResponseSerializer
from fuel.services.corridor import find_candidate_stops
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
    request.
    """

    def post(self, request):
        request_serializer = TripPlanRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        start_query = request_serializer.validated_data["start"]
        finish_query = request_serializer.validated_data["finish"]

        try:
            start_coords = geocode(start_query)
            finish_coords = geocode(finish_query)
            route = get_route(start_coords, finish_coords)
            candidates = find_candidate_stops(route)
            plan = plan_refueling(candidates, route.distance_miles)
        except LocationNotFoundError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except (RouteNotFoundError, InfeasibleRouteError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except UpstreamServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        payload = {
            "distance_miles": round(route.distance_miles, 1),
            "route": {"type": "LineString", "coordinates": [list(c) for c in route.coordinates]},
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
