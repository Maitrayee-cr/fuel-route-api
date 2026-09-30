import json
import math
from functools import lru_cache
import numpy as np
from django.conf import settings
from shapely import prepare
from shapely.geometry import Point, shape
from .errors import PlanningError

METERS_PER_MILE = 1609.344
EARTH_MILES = 3958.7613


@lru_cache(maxsize=1)
def usa_boundary():
    with open(settings.BASE_DIR / 'data/usa.geojson') as f:
        boundary = shape(json.load(f)['geometry'])
    prepare(boundary)
    return boundary


def validate_coordinate(value):
    if not isinstance(value, dict) or set(value) != {'lat', 'lon'}:
        raise PlanningError('invalid_location', 'Coordinates must contain exactly lat and lon.', 400)
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in value.values()):
        raise PlanningError('invalid_location', 'Coordinates must be finite numbers.', 400)
    lat, lon = value['lat'], value['lon']
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or not usa_boundary().covers(Point(lon, lat)):
        raise PlanningError('outside_usa', 'Both locations must be within the USA.', 400)
    return (float(lon), float(lat))


def haversine(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    q = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 2 * EARTH_MILES * math.asin(min(1, math.sqrt(q)))


def route_candidates(route, stations, corridor_miles):
    """Local tangent-plane projection per segment, vectorized over geometry.

    Haversine segment lengths are scaled to provider route distance. This
    approximation is used ONLY for candidate selection; final purchased fuel
    uses routed waypoint leg distances, including detours.
    """
    coords = np.asarray(route['geometry']['coordinates'], dtype=float)
    if len(coords) < 2:
        return []
    a, b = coords[:-1], coords[1:]
    scale_x = 69.0934 * np.cos(np.deg2rad((a[:, 1] + b[:, 1])/2))
    scale_y = 69.0934
    dx = (b[:, 0] - a[:, 0]) * scale_x
    dy = (b[:, 1] - a[:, 1]) * scale_y
    # Vectorize once per route rather than calling Python for every road segment.
    radians = np.deg2rad(coords)
    delta = np.diff(radians, axis=0)
    q = np.sin(delta[:, 1] / 2) ** 2 + np.cos(radians[:-1, 1]) * np.cos(radians[1:, 1]) * np.sin(delta[:, 0] / 2) ** 2
    lengths = 2 * EARTH_MILES * np.arcsin(np.sqrt(np.clip(q, 0, 1)))
    cumulative = np.r_[0.0, np.cumsum(lengths)]
    if cumulative[-1] <= 0:
        return []
    factor = route['distance'] / METERS_PER_MILE / cumulative[-1]
    denom = dx*dx + dy*dy
    output = []
    for station in stations:
        px = (station.longitude - a[:, 0]) * scale_x
        py = (station.latitude - a[:, 1]) * scale_y
        t = np.clip(np.divide(px*dx + py*dy, denom, out=np.zeros_like(dx), where=denom>0), 0, 1)
        distance = np.hypot(px - t*dx, py - t*dy)
        index = int(np.argmin(distance))
        if distance[index] <= corridor_miles:
            mile = (cumulative[index] + t[index]*lengths[index]) * factor
            output.append((station, float(mile), float(distance[index])))
    return output
