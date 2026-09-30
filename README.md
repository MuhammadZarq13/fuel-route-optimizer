# Fuel Route Trip Planner

A Django + DRF API that, given a start and finish location in the USA,
returns:

- the driving route (as GeoJSON, for plotting on a map),
- an optimal, cost-minimizing set of fuel stops that respects a 500-mile
  vehicle range, and
- the total money spent on fuel, assuming 10 miles per gallon.

Fuel prices come from the supplied OPIS truck-stop price list
(`fuel/data/fuel_prices.csv`, ~8,150 stations).

## Quickstart

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py load_fuel_prices   # loads fuel/data/fuel_prices.csv, ~instant
python manage.py runserver
```

Django 5.2 requires Python 3.10+; use `python3.12`/`python3.11` explicitly
if your default `python3` is older.

Then:

```bash
curl -X POST http://127.0.0.1:8000/api/trip-plan/ \
  -H "Content-Type: application/json" \
  -d '{"start": "Chicago, IL", "finish": "Denver, CO"}'
```

A Postman collection is included at `postman_collection.json` (import it
directly, or use it as the basis for the Loom demo).

## API

`POST /api/trip-plan/`

Request:
```json
{ "start": "Chicago, IL", "finish": "Denver, CO" }
```

Response `200`:
```json
{
  "distance_miles": 1004.3,
  "route": { "type": "LineString", "coordinates": [[-87.62, 41.88], ...] },
  "fuel_stops": [
    {
      "name": "KWIK STAR #932", "address": "...", "city": "Altoona", "state": "IA",
      "latitude": 41.64, "longitude": -93.46, "price_per_gallon": "2.959",
      "distance_along_route_miles": 321.8, "gallons_purchased": 32.18, "cost": 95.22
    }
  ],
  "total_gallons_purchased": 50.43,
  "total_fuel_cost": 153.03
}
```

Error responses:
- `400` — a location string couldn't be parsed/geocoded, or the request body is invalid.
- `422` — a valid route exists but no feasible sequence of fuel stops covers it (a gap between stations exceeds the vehicle's range).
- `502` — the upstream routing/geocoding provider failed or timed out.

## Architecture

```
POST /api/trip-plan/
        │
        ▼
  geocode(start)  ──┐
  geocode(finish) ──┤  Nominatim (OpenStreetMap), cached per query
        │           │
        ▼           │
   get_route()  ─────┘  OSRM public routing server — ONE call, returns
        │               full route geometry + total distance
        ▼
 find_candidate_stops()  Bounding-box DB prefilter (indexed) + Haversine
        │                distance-to-route check → stations near the
        │                corridor, each tagged with its mile-marker
        ▼                position along the route
  plan_refueling()   Greedy optimal-refueling algorithm (pure Python,
        │             no I/O) → which stations to buy at and how much
        ▼
     Response
```

**External API budget**: at most 3 calls per request (2 geocodes + 1
route), and fewer on a cache hit — well inside the "one ideal, two or
three acceptable" limit. Fuel station coordinates are **not** geocoded
per request; see "Fuel station geocoding" below. The two geocode calls
are independent (start/finish), so they run concurrently on a small
thread pool (`fuel/views.py`) instead of back-to-back — measured this
against a real Chicago→Denver-style transcontinental request and it cut
that portion of latency roughly in half. Both HTTP clients also share a
single `requests.Session` with automatic retry on transient failures
(`fuel/services/http.py`) — we hit real connection resets from Nominatim
under load during development, so this isn't hypothetical.

The `route` field in the response is the **downsampled** polyline
(~1 point per 2 miles, the same points used internally to place fuel
stations), not OSRM's raw geometry. On a cross-country route OSRM can
return 30,000+ raw points; a map doesn't need that much detail, and
shipping it nearly 20x'd the response size for no benefit (measured:
786 KB → 34 KB on a 2,800-mile LA→NYC route with no loss of visual
fidelity).

### Data model

`fuel.models.FuelStation` — one row per CSV line: OPIS ID, name, address,
city, state, rack ID, price per gallon, and resolved latitude/longitude
(nullable). Indexed on `(state, city)` and on `(latitude, longitude)` for
the corridor bounding-box query. The bounding-box filter is written as a
plain `latitude__range`/`longitude__range` query so it works unchanged on
SQLite (used here for a zero-setup demo) or PostgreSQL.

`load_fuel_prices` refuses to run against a non-empty table unless
`--clear` is passed — `bulk_create` doesn't dedupe, and the source CSV
itself has legitimate duplicate OPIS IDs for renamed/rebranded stops, so
silently re-running the loader would double every row rather than error.

**Corridor scalability**: a single bounding box around an entire route is
tight for a north-south/east-west route but barely filters anything for a
diagonal one — profiling a real LA→NYC request showed a whole-route bbox
matched 1,863 of 3,575 geocoded stations (52% of the whole table) before
the precise per-station Haversine pass even started. Fixed by chunking the
route into ~100-mile segments, each with its own tight bbox, OR'd together
into one query (`ROUTE_SEGMENT_LENGTH_MILES` in `fuel/services/corridor.py`)
— the per-station exact-distance pass then only scans points from the
segment(s) that station's bbox actually matched, not the whole route.
Measured on the same LA→NYC request: corridor filtering went from 0.99s
to 0.03s (37x), full cold-cache request from 6.0s to 2.8s, with
byte-identical output. Candidate count now tracks route length and
station density near the route, not total table size or how diagonal the
route is — it won't get worse as geocoding coverage grows toward 100%.
At genuinely large scale (a much bigger station table, e.g. hundreds of
thousands of rows), the next step past this would be PostGIS — store the
route as a `LineString` and use one indexed `ST_DWithin` query instead of
segmented bounding boxes — but that wasn't needed to fix the problem that
was actually measured here.

### Fuel station geocoding

The CSV has no coordinates — only city/state. Geocoding a station on
every request (or even once per load) against 8,151 rows isn't
reasonable against Nominatim's 1 req/s usage policy. Instead:

- `python manage.py build_geocode_cache` is a one-time, re-runnable batch
  job that geocodes each of the ~3,900 **unique** City+State pairs (not
  each row) and writes `fuel/data/geocode_cache.json`.
- That file is committed to the repo, so `load_fuel_prices` joins the CSV
  to it and loads instantly, fully offline — no network access needed to
  set up the project.
- This means station coordinates are **city-center precision**, not exact
  truck-stop addresses. The route corridor width (`CORRIDOR_WIDTH_MILES`
  in `fuel/services/corridor.py`) is set wide enough (15 miles) to absorb
  that imprecision; the real fix, if exact station coordinates were
  available, would be to tighten it back down.
- The committed cache covers **~1,715 of the ~3,900** unique City+State
  pairs (~44% — enough that every route exercised in testing/the demo
  returns multiple real, differently-priced candidate stops). Geocoding
  the remainder against a free, rate-limited (1 req/s) API is a genuine
  ~40-minute wall-clock job with no way to safely parallelize it against
  a single public Nominatim instance, so it wasn't run to full completion
  for this exercise. Extending coverage is a single command:
  `python manage.py build_geocode_cache` (resumable — it only geocodes
  pairs not already in the cache). A station with no cached coordinates
  is loaded with `latitude`/`longitude` as `null` and simply excluded from
  route matching rather than causing an error.

### The refueling algorithm

This is the classic "optimal refueling" problem: minimize total fuel cost
across a fixed number of gallons ($ = \text{distance} / \text{mpg}$),
where the only choice is *where* each gallon is bought, subject to never
exceeding the vehicle's range between fill-ups.

Provably-optimal greedy (`fuel/services/refueling.py::plan_refueling`):
at every station, look ahead exactly as far as a full tank would reach.
- If a **cheaper** station exists within that range, buy only enough fuel
  here to reach it — never overpay for gas you didn't need to buy at this
  price.
- Otherwise, this station is the cheapest price available in its own
  look-ahead window, so fill the tank **completely** (or just enough to
  finish the trip, if the destination is closer than a full tank).

Buying the minimum necessary whenever something cheaper is ahead, and
maximizing purchase at each local price minimum, dominates any other
feasible strategy — swapping a purchase for a more expensive one
elsewhere can only raise the total cost. See `fuel/tests/test_refueling.py`
for a hand-verified worked example.

**Assumptions** (undetermined by the assignment, stated explicitly):
the vehicle starts the trip with a full tank (so trips under 500 miles
need no fuel stop at all), and fuel remaining in the tank at arrival is a
sunk cost, not credited back.

### Caching

- Geocode results: cached 24h (`GEOCODE_CACHE_TTL_SECONDS`) — a city's
  coordinates don't change.
- Route results: cached 1h (`ROUTE_CACHE_TTL_SECONDS`), keyed on rounded
  coordinates — repeat trip requests for the same start/finish skip the
  external routing call entirely.
- Uses Django's cache framework (`django.core.cache`) with the in-memory
  backend for this demo; swapping in Redis is a one-line `CACHES` change
  in `config/settings.py`, no application code changes needed.

## Tests

```bash
python manage.py test
```

17 tests covering: the refueling algorithm against hand-computed
scenarios (including infeasible-route detection), the corridor filter's
geometry, and the API endpoint end-to-end with the geocoding/routing
calls mocked (no live network access needed to run the suite).

## Project layout

```
config/                  Django project settings/urls
fuel/
  models.py              FuelStation
  management/commands/
    build_geocode_cache.py   one-time offline geocoding batch job
    load_fuel_prices.py      CSV + geocode cache -> FuelStation rows
  services/
    geocoding.py          Nominatim client (cached)
    routing.py             OSRM client (cached)
    corridor.py             route corridor / candidate station filtering
    refueling.py            the optimal refueling algorithm
    exceptions.py
  serializers.py, views.py  DRF request/response layer
  data/
    fuel_prices.csv         supplied fuel price data
    geocode_cache.json       pre-baked City+State -> lat/lon
  tests/
```
