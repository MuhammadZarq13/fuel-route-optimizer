import hashlib

import requests
from django.conf import settings
from django.core.cache import cache

from fuel.services.exceptions import LocationNotFoundError, UpstreamServiceError
from fuel.services.http import SESSION

USER_AGENT = "django-fuel-route-planner/1.0 (coding assessment)"
REQUEST_TIMEOUT_SECONDS = 8


def geocode(location: str) -> tuple[float, float]:
    """Resolve a free-text US location string to (latitude, longitude).

    Results are cached (keyed on the normalized query) so repeat requests
    for the same location never hit Nominatim twice.
    """
    normalized = location.strip().lower()
    digest = hashlib.sha1(normalized.encode()).hexdigest()
    cache_key = f"geocode:{digest}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached["lat"], cached["lon"]

    params = {
        "q": location,
        "format": "json",
        "limit": 1,
        "countrycodes": "us",
    }
    try:
        response = SESSION.get(
            f"{settings.NOMINATIM_BASE_URL}/search",
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        results = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise UpstreamServiceError(f"Geocoding request failed for {location!r}: {exc}") from exc

    if not results:
        raise LocationNotFoundError(f"Could not geocode location: {location!r}")

    lat, lon = float(results[0]["lat"]), float(results[0]["lon"])
    cache.set(cache_key, {"lat": lat, "lon": lon}, settings.GEOCODE_CACHE_TTL_SECONDS)
    return lat, lon
