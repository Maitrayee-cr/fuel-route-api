import hashlib
import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from django.conf import settings
from django.core.cache import cache
from .errors import PlanningError
from .geometry import validate_coordinate
from .models import ProviderGate
from .locations import local_place


def reserve_request(provider, interval=1.05):
    """Atomic compare-and-swap; shared SQLite DB also protects multiple workers."""
    ProviderGate.objects.get_or_create(provider=provider)
    deadline = time.monotonic() + settings.PROVIDER_QUEUE_SECONDS
    while time.monotonic() < deadline:
        previous = ProviderGate.objects.get(pk=provider).next_allowed
        now = time.time()
        if previous > now:
            time.sleep(min(previous-now, 0.1))
            continue
        if ProviderGate.objects.filter(pk=provider, next_allowed=previous).update(next_allowed=now+interval):
            return
    raise PlanningError('provider_busy', 'Routing service is busy; try again shortly.', 503)


def get_json(url, provider):
    reserve_request(provider)
    request = Request(url, headers={'User-Agent': settings.HTTP_USER_AGENT, 'Accept': 'application/json'})
    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read(12_000_001)
            if len(payload) > 12_000_000:
                raise ValueError('oversized response')
            return json.loads(payload)
    except HTTPError as exc:
        raise PlanningError('upstream_error', f'{provider} returned HTTP {exc.code}.', 503 if exc.code==429 else 502) from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        # Never echo the URL: geocoding URLs include the server-side API key.
        raise PlanningError('upstream_error', f'{provider} did not return a usable response.', 502) from exc


def geocode_query(query):
    if not settings.GEOAPIFY_API_KEY:
        raise PlanningError('geocoding_not_configured', 'Set GEOAPIFY_API_KEY, or submit latitude/longitude coordinates.', 503)
    params = urlencode({'text': query, 'filter': 'countrycode:us', 'format': 'json', 'limit': 1, 'apiKey': settings.GEOAPIFY_API_KEY})
    return get_json('https://api.geoapify.com/v1/geocode/search?' + params, 'geocoding')


class Provider:
    def __init__(self):
        self.routing_calls = 0
        self.geocoding_calls = 0
        self.cache_hits = 0
        self.local_geocodes = 0

    def location(self, value):
        if isinstance(value, dict):
            return validate_coordinate(value)
        if not isinstance(value, str) or not 2 <= len(value.strip()) <= 250:
            raise PlanningError('invalid_location', 'Use a US place name or an object with lat and lon.', 400)
        query = ' '.join(value.strip().split())
        local = local_place(query)
        if local is not None:
            self.local_geocodes += 1
            return validate_coordinate({'lon': local[0], 'lat': local[1]})
        key = 'geo-v2:' + hashlib.sha256(query.casefold().encode()).hexdigest()
        cached = cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            return tuple(cached)
        self.geocoding_calls += 1
        data = geocode_query(query)
        try:
            item = data['results'][0]
            if item['country_code'] != 'us':
                raise ValueError('not US')
            point = validate_coordinate({'lat': item['lat'], 'lon': item['lon']})
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PlanningError('location_not_found', 'Could not resolve a US location; use precise coordinates.', 400) from exc
        cache.set(key, point, 30*86400)
        return point

    def route(self, points):
        if len(points) > settings.MAX_WAYPOINTS:
            raise PlanningError('too_many_stops', 'This route needs too many stops for the public routing provider.')
        coordinates = ';'.join(f'{lon:.6f},{lat:.6f}' for lon, lat in points)
        # City representative points may be away from a road; stations stay tight.
        radiuses = [str(settings.ENDPOINT_SNAP_METERS), *(['300'] * (len(points)-2)), str(settings.ENDPOINT_SNAP_METERS)]
        key = 'route-v2:' + hashlib.sha256((settings.OSRM_URL+coordinates+','.join(radiuses)).encode()).hexdigest()
        cached = cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            return cached
        self.routing_calls += 1
        params = urlencode({'overview': 'full', 'geometries': 'geojson', 'steps': 'false', 'radiuses': ';'.join(radiuses)})
        data = get_json(f'{settings.OSRM_URL}/route/v1/driving/{coordinates}?{params}', 'routing')
        if not isinstance(data, dict):
            raise PlanningError('upstream_error', 'Routing response was malformed.', 502)
        if data.get('code') != 'Ok':
            raise PlanningError('no_route', 'No drivable route found near the endpoints and selected stations.')
        try:
            route = data['routes'][0]
            numeric = [route['distance'], route['duration'], *[leg['distance'] for leg in route['legs']]]
            if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) or x < 0 for x in numeric):
                raise ValueError('bad distances')
            if len(route['legs']) != len(points)-1 or route['geometry']['type'] != 'LineString':
                raise ValueError('bad legs')
            coords = route['geometry']['coordinates']
            if len(coords) < 2 or any(not isinstance(c, (list, tuple)) or len(c)!=2 or any(isinstance(v, bool) or not isinstance(v, (float,int)) or not math.isfinite(v) for v in c) or not -180 <= c[0] <= 180 or not -90 <= c[1] <= 90 for c in coords):
                raise ValueError('bad geometry')
            if abs(sum(leg['distance'] for leg in route['legs']) - route['distance']) > max(5, len(points)):
                raise ValueError('distance mismatch')
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PlanningError('upstream_error', 'Routing response was malformed.', 502) from exc
        cache.set(key, route, settings.ROUTE_CACHE_SECONDS)
        return route
