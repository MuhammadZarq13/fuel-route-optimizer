class TripPlanningError(Exception):
    """Base class for errors raised while planning a trip."""


class LocationNotFoundError(TripPlanningError):
    """A start/finish location string could not be geocoded."""


class RouteNotFoundError(TripPlanningError):
    """The routing provider could not find a drivable route."""


class UpstreamServiceError(TripPlanningError):
    """A network/HTTP failure talking to an external API."""


class InfeasibleRouteError(TripPlanningError):
    """No sequence of fuel stops can cover the route within vehicle range."""
