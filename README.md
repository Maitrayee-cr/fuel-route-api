# Fuelwise

A Django API for planning fuel stops on US road trips. It takes a start and
destination, finds a driving route, and returns fuel purchases and an interactive
map. The vehicle gets 10 miles per gallon and has a 500-mile range, so the tank
holds 50 gallons.

Prices come from the supplied assessment CSV. Of its 6,626 US station IDs, 1,396
have verified coordinates and can be used by the planner. Recommendations are
cost-optimized among those stations, not guaranteed cheapest across the entire
dataset.

## Run locally

Use Python 3.12 or newer. Internet access is needed for routing and map tiles;
city/state and coordinate inputs do not require an API key.

From the project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createcachetable
python manage.py import_stations data/fuel-prices.csv
python manage.py import_coordinates data/verified-coordinates.json
python manage.py runserver
```

On Windows, use `python` instead of `python3` and activate the environment with
`.venv\Scripts\activate.bat` in Command Prompt, or
`.venv\Scripts\Activate.ps1` in PowerShell.

Open http://127.0.0.1:8000. Try `Chicago, IL` to `Atlanta, GA`. Enter a starting
fuel price if you want the value of all fuel consumed, rather than just the cost
of purchases during the trip.

Docker is also available for the local demo:

```bash
docker build -t fuelwise .
docker run --rm -p 8000:8000 fuelwise
```

The container imports the data at startup and runs Django's development server.
Its database is temporary unless you configure persistent storage.

### Configuration

Settings are read from environment variables. `.env.example` lists the options;
copying it to `.env` does **not** load them automatically. Export variables in the
same shell that starts Django, or configure them in your deployment environment.

For street-address searches, set an optional Geoapify key before starting Django:

```bash
export GEOAPIFY_API_KEY='your-key'
```

`OSRM_URL` can point to another compatible OSRM service. The default is
`https://routing.openstreetmap.de/routed-car`.

Local defaults are for development only. For deployment, set a private
`DJANGO_SECRET_KEY`, disable `DJANGO_DEBUG`, configure `DJANGO_ALLOWED_HOSTS`, and
use a production server. Keep real keys out of `.env.example` and Git.

## API

Create a trip with `POST /api/routes/` and an `application/json` body:

```json
{
  "start": "Chicago, IL",
  "finish": "Atlanta, GA",
  "initial_fuel_gallons": 50,
  "initial_fuel_price": 3.5
}
```

Only `start` and `finish` are required. Each accepts a place string or a coordinate
object such as `{"lat": 41.8781, "lon": -87.6298}`. Include the state when using a
city name; ambiguous names return an error. Starting fuel defaults to 50 gallons
and must be between 0 and 50. Starting price is optional, in USD per gallon.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/` | Trip form |
| POST | `/api/routes/` | Create a plan; returns 201 on success |
| GET | `/api/routes/{id}/` | Retrieve a saved plan |
| GET | `/maps/{id}/` | Open its interactive map |
| GET | `/api/health/` | Status and station counts |

The response includes purchases, actual driving legs, fuel balances, coverage,
warnings, and external-call counts. `route` is a GeoJSON **Feature**, with the
road LineString in `route.geometry`. `map_url` links to the saved map.

Here is an excerpt from the recorded Los Angeles–Oklahoma City response, using
50 starting gallons priced at $3.50 per gallon. Other fields are omitted:

```json
{
  "distance_miles": 1330.7990709257933,
  "fuel_purchased_cost": "262.03",
  "fuel_consumed_gallons": 133.07990709257933,
  "total_trip_fuel_cost": "437.03",
  "arrival_gallons": 0.0,
  "external_calls": {
    "routing": 2,
    "geocoding": 0,
    "cache_hits": 0
  }
}
```

See the [full recorded response](docs/live-example-response.json),
[OpenAPI specification](docs/openapi.json), and
[Postman collection](docs/postman_collection.json). The recorded trip ID and map
URL belong to the test environment; create a new trip to get a working map link.

### What the costs mean

- `fuel_purchased_cost`: cash spent buying fuel during the trip. It excludes fuel
  already in the tank.
- `fuel_consumed_gallons`: actual routed miles divided by 10.
- `total_trip_fuel_cost`: value of fuel consumed, including the starting fuel used
  at the supplied `initial_fuel_price`. It is `null` when starting fuel is consumed
  but its price is unknown. The map displays this as “Unknown,” not zero.

For example, a 200-mile trip starting with 50 gallons needs no new purchase. It
uses 20 gallons; at a supplied starting price of $3.50, the consumed fuel costs
$70. The other 30 starting gallons are not charged to that trip.

`total_fuel_cost` remains a compatibility alias for `fuel_purchased_cost`, and
`gallons_consumed` aliases `fuel_consumed_gallons`. New clients should use the
explicit names above. `total_cash_including_initial_fuel`, when available,
includes the value of the entire starting tank, even fuel left over; it is not
the same as consumed-fuel cost. Monetary transactions use Decimal and are rounded
to cents.

## How planning works

1. Validate the inputs and resolve city/state names from the bundled US Census
   place data. Other text inputs can use Geoapify when configured. Check endpoint
   coordinates against US boundaries.
2. Request a base driving route from OSRM. Filter verified stations by a bounding
   box, then project nearby candidates onto the route. Start with a 2-mile
   corridor; widen to 10 miles locally if no feasible fuel plan is found.
3. Order candidates by route progress and calculate fuel purchases. Buy only
   enough to reach the first reachable cheaper station. If there is none, buy up
   to the tank capacity, capped by the fuel needed to reach the destination.
   Existing fuel is counted before deciding how much to buy.
4. Request one route through the selected stations and recalculate fuel using its
   actual road distances. If detours make the plan infeasible, try an alternative
   sequence with a conservative planning range and at most one more routing call.
   Final calculations always use the actual 50-gallon tank and 10 mpg.
5. Save the result. Map and detail views read this saved result without routing
   again.

Sorting takes O(n log n). A monotonic stack finds the next cheaper station in
O(n), followed by a forward pass to calculate purchases. This fuel-allocation
rule is optimal for a fixed ordered route with continuous fuel quantities,
unlimited station supply and no stop fees. Selecting the station sequence is
heuristic: the planner does not compare every station combination or road route.

### Routing calls and caching

| Request | Routing calls |
| --- | ---: |
| New route needing no fuel stop | 1 |
| New route with fuel stops | Usually 2 |
| Detour recovery | At most 3 |
| Repeat with all required routing responses cached | 0 |
| Saved map or detail view | 0 |

Route responses are cached for 24 hours in Django's database cache. Local city
lookup and prepared station coordinates avoid per-trip station geocoding.
NumPy handles route-segment calculations, and a database-backed rate limiter
coordinates provider access. Remote address geocoding and browser map-tile
requests are separate from the routing calls above.

## Station data

The original CSV is preserved in `data/fuel-prices.csv`. Importing excludes
non-US records and deduplicates by OPIS station ID. There are 487 IDs with
conflicting prices; the importer uses the highest supplied price and retains all
source prices. This is a conservative assumption, not a claim about the current
pump price.

The CSV has no coordinates. The bundled coordinate file matches 1,396 stations
to official Pilot/Flying J, TA/Petro and Love's location records. Matching uses
station identity and corroborating address information; ambiguous matches are
excluded. City-center coordinates are never substituted for station locations.
Sources and matching details are recorded in
`data/verified-coordinates.json` and [data/provenance.json](data/provenance.json).

Coordinate enrichment runs before trip requests. The management commands support
geocoding proposals, exporting them for review, and importing reviewed coordinates.
`scripts/build_reference_data.py` rebuilds reference data from downloaded source
files; run it with `--help` for its inputs.

## Code layout

| Location | Responsibility |
| --- | --- |
| `config/` | Django settings and URLs |
| `planner/views.py` | HTTP validation, responses and map rendering |
| `planner/service.py` | Trip-planning workflow |
| `planner/optimizer.py` | Fuel allocation |
| `planner/providers.py` | Routing/geocoding requests, caching and rate limiting |
| `planner/geometry.py` | Boundary checks and route projection |
| `planner/locations.py` | Local city/state lookup |
| `planner/models.py` | Stations, saved trips and provider-rate state |
| `planner/management/commands/` | Data imports and coordinate review |
| `planner/templates/` | Homepage and Leaflet map |
| `planner/tests/` | Automated tests |
| `scripts/` | Data preparation and live/browser checks |

The backend uses Django 6.1.1, SQLite, NumPy and Shapely. The frontend uses Django
templates and Leaflet; it does not need a frontend build step.

## Tests

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

The 49 automated tests cover input validation, imports, fuel accounting,
optimization, caching, provider errors, detours and saved routes. They make no
external requests. Randomized optimizer cases are checked against an independent
dynamic-programming solver.

For live checks, use a prepared test database and an internet connection:

```bash
python scripts/verify_live.py --output docs/verification.json
```

This creates saved trips. The [recorded checks](docs/verification.json) cover eight
route scenarios and cached repeats, including fuel balances, purchase totals,
500-mile leg limits, map/detail responses and routing-call counts. Timings are
individual observations, not latency guarantees.

An optional browser check is in `scripts/verify_browser.cjs`. It needs Playwright,
a browser installation and a running server; `BASE_URL` and `BROWSER_EXECUTABLE`
configure the target. See [docs/DEMO.md](docs/DEMO.md) for the walkthrough outline.

## Limitations

- **Coverage:** 5,230 US station IDs still lack verified coordinates. Missing
  stations may be cheaper or necessary for a feasible trip. A 422 response means
  no feasible plan was found with available data, not that the journey is
  impossible in the real world.
- **Selection:** corridor projection and bounded replanning do not guarantee a
  global minimum. Looping routes and stations outside the corridor can produce
  missed alternatives.
- **Prices:** the supplied CSV is a snapshot, not a live price feed.
- **Routing:** the car profile does not account for truck height, weight or
  hazardous-material restrictions. US endpoints are checked, but intermediate
  roads are not guaranteed to remain inside the US.
- **Availability:** the public routing service and map tiles need internet access.
  The local server and Docker setup are for the assessment demo, not production.

## Sources

- Routing: [OSRM](https://project-osrm.org/) and
  [FOSSGIS](https://routing.openstreetmap.de/).
- Maps: [Leaflet](https://leafletjs.com/) and
  [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).
- Optional address geocoding: [Geoapify](https://www.geoapify.com/).
- Boundaries and place lookup: [US Census Bureau](https://www.census.gov/geographies/).
- Station coordinate sources: [reference-data provenance](data/provenance.json).
