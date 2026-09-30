import math
from decimal import Decimal

from django.conf import settings
from .errors import PlanningError
from .geometry import METERS_PER_MILE, haversine, route_candidates
from .models import Station
from .optimizer import Stop, optimize, money
from .providers import Provider


def routed_plan(provider, start, finish, selected, base):
    route = provider.route([start, *[(s.longitude, s.latitude) for s in selected], finish]) if selected else base
    position, stops, visits = 0.0, [], []
    for station, leg in zip(selected, route['legs']):
        position += leg['distance'] / METERS_PER_MILE
        stops.append(Stop(station.opis_id, position, station.price))
        visits.append({'station_id': station.opis_id, 'name': station.name,
                       'lat': station.latitude, 'lon': station.longitude, 'mile': position})
    return route, stops, visits


def fuel_accounting(plan, initial_fuel, initial_price):
    """Report cash and consumption separately, without inventing an initial price."""
    used = min(float(initial_fuel), plan['gallons_consumed'])
    initial_cost = money(Decimal(str(used)) * Decimal(str(initial_price))) if initial_price is not None else None
    consumed_cost = (money(Decimal(plan['total_fuel_cost']) + Decimal(initial_cost))
                     if initial_cost is not None else plan['total_fuel_cost'] if used == 0 else None)
    return {
        'basis': 'En-route purchases; starting fuel is already owned.',
        'en_route_cost': plan['total_fuel_cost'],
        'initial_gallons_consumed': used,
        'initial_fuel_consumed_cost': initial_cost,
        'total_consumed_fuel_cost': consumed_cost,
        'initial_price_required': used > 0 and initial_price is None,
    }


def plan_trip(payload):
    if not isinstance(payload, dict):
        raise PlanningError('invalid_request', 'The JSON body must be an object.', 400)
    if set(payload) - {'start', 'finish', 'initial_fuel_gallons', 'initial_fuel_price'}:
        raise PlanningError('invalid_request', 'Unknown request field.', 400)
    if 'start' not in payload or 'finish' not in payload:
        raise PlanningError('invalid_request', 'start and finish are required.', 400)
    fuel = payload.get('initial_fuel_gallons', 50)
    initial_price = payload.get('initial_fuel_price')
    for field, value, upper in [('initial_fuel_gallons', fuel, 50), ('initial_fuel_price', initial_price, 100)]:
        if value is None and field == 'initial_fuel_price':
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= upper:
            raise PlanningError('invalid_request', f'{field} must be a finite number between 0 and {upper}.', 400)
    provider = Provider()
    start, finish = provider.location(payload['start']), provider.location(payload['finish'])
    if haversine(start, finish) < 0.05:
        raise PlanningError('same_location', 'Start and finish must be at least 0.05 miles apart.', 400)
    all_count = Station.objects.count()
    ready_count = Station.objects.filter(geocode_status='verified').count()
    if all_count == 0:
        raise PlanningError('data_not_loaded', 'Import the supplied fuel-price CSV first.', 503)
    base = provider.route([start, finish])
    base_miles = base['distance'] / METERS_PER_MILE
    # A latitude/longitude bounding box uses the DB index before exact segment projection.
    coords = base['geometry']['coordinates']
    lons, lats = zip(*coords)
    lon_margin = settings.FALLBACK_CORRIDOR_MILES / (69.0 * max(0.1, math.cos(math.radians(max(abs(min(lats)), abs(max(lats)))))))
    lat_margin = settings.FALLBACK_CORRIDOR_MILES / 69.0
    stations = list(Station.objects.filter(geocode_status='verified',
                    latitude__range=(min(lats)-lat_margin, max(lats)+lat_margin),
                    longitude__range=(min(lons)-lon_margin, max(lons)+lon_margin)))
    broad_candidates = route_candidates(base, stations, settings.FALLBACK_CORRIDOR_MILES)
    corridor = settings.CORRIDOR_MILES
    candidates = [candidate for candidate in broad_candidates if candidate[2] <= corridor]
    try:
        projected = optimize([Stop(s.opis_id, mile, s.price) for s, mile, _ in candidates], base_miles, fuel)
    except PlanningError:
        # Widen locally, using the same route geometry and zero extra API calls.
        candidates, corridor = broad_candidates, settings.FALLBACK_CORRIDOR_MILES
        projected = optimize([Stop(s.opis_id, mile, s.price) for s, mile, _ in candidates], base_miles, fuel)
    by_id = {s.opis_id: s for s, _, _ in candidates}
    selected_ids = [p['station_id'] for p in projected['purchases']]
    selected = [by_id[i] for i in selected_ids]
    final_route, verified_stops, visits = routed_plan(provider, start, finish, selected, base)
    replanned = False
    try:
        plan = optimize(verified_stops, final_route['distance'] / METERS_PER_MILE, fuel)
    except PlanningError as exc:
        # Spend at most one extra routing call on a more conservative sequence.
        # The reduced planning capacity reserves distance for access roads; the
        # final allocation ALWAYS uses the real 50-gallon tank and driving legs.
        alternative = None
        for capacity in (48, 46, 44):
            try:
                proposal = optimize([Stop(s.opis_id, mile, s.price) for s, mile, _ in candidates],
                                    base_miles, min(fuel, capacity), capacity=capacity)
            except PlanningError:
                continue
            ids = [p['station_id'] for p in proposal['purchases']]
            if ids != selected_ids and len(ids) + 2 <= settings.MAX_WAYPOINTS:
                alternative = [by_id[i] for i in ids]
                break
        if alternative is None:
            raise PlanningError('detour_exceeds_range', 'Driving detours exceed available range and no alternative station sequence was found.') from exc
        final_route, verified_stops, visits = routed_plan(provider, start, finish, alternative, base)
        try:
            plan = optimize(verified_stops, final_route['distance'] / METERS_PER_MILE, fuel)
        except PlanningError as retry_exc:
            raise PlanningError('detour_exceeds_range', 'Both selected station sequences exceed available range after driving-distance validation.') from retry_exc
        replanned = True
    miles = final_route['distance'] / METERS_PER_MILE
    for purchase in plan['purchases']:
        station = by_id[purchase['station_id']]
        purchase.update({'name': station.name, 'address': station.address,
                         'city': station.city, 'state': station.state,
                         'lat': station.latitude, 'lon': station.longitude,
                         'coordinate_source': station.coordinate_source})
    warnings = ['Prices are a snapshot from the supplied CSV, not live pump prices.',
                f'Fuel allocation is minimized on the selected stop sequence; station selection uses a {corridor:g}-mile corridor heuristic, not a global road-network optimum.',
                'Road routing uses a car profile; truck height, weight, hazardous-material and access restrictions are not modeled.']
    if ready_count < all_count:
        warnings.append(f'Partial station coverage: {ready_count} of {all_count} US stations have verified coordinates; excluded stations may offer better prices.')
    if initial_price is None:
        warnings.append('Initial tank fuel is already owned; total_fuel_cost counts en-route purchases only. Supply initial_fuel_price to value starting fuel separately.')
    initial_value = money(Decimal(str(fuel)) * Decimal(str(initial_price))) if initial_price is not None else None
    accounting = fuel_accounting(plan, fuel, initial_price)
    return {
        'start': {'lon': start[0], 'lat': start[1]}, 'finish': {'lon': finish[0], 'lat': finish[1]},
        'distance_miles': miles, 'base_distance_miles': base_miles,
        'detour_miles': miles-base_miles, 'duration_seconds': final_route['duration'],
        'currency': 'USD', 'mpg': 10, 'tank_capacity_gallons': 50, 'range_miles': 500,
        'initial_fuel_gallons': fuel, **plan,
        'fuel_purchased_cost': plan['total_fuel_cost'],
        'fuel_consumed_gallons': plan['gallons_consumed'],
        'total_trip_fuel_cost': accounting['total_consumed_fuel_cost'],
        'fuel_accounting': accounting,
        'initial_fuel_value': initial_value,
        'total_cash_including_initial_fuel': money(Decimal(plan['total_fuel_cost'])+Decimal(initial_value)) if initial_value is not None else None,
        'route': {'type': 'Feature', 'properties': {}, 'geometry': final_route['geometry']},
        'visits': visits,
        'legs': [{'distance_miles': leg['distance']/METERS_PER_MILE} for leg in final_route['legs']],
        'coverage': {'total_us_stations': all_count, 'verified_stations': ready_count, 'corridor_candidates': len(candidates), 'corridor_miles': corridor},
        'external_calls': {'routing': provider.routing_calls, 'geocoding': provider.geocoding_calls, 'cache_hits': provider.cache_hits},
        'local_geocodes': provider.local_geocodes,
        'detour_replanned': replanned,
        'optimality': 'Cost-optimized among available verified stations. Exact fuel allocation on the final fixed stop sequence; heuristic selection of that sequence, not a global optimum.',
        'warnings': warnings,
        'attribution': 'Routing: OSRM / FOSSGIS. Map data: OpenStreetMap contributors. Remote geocoding: Geoapify. Boundaries and city lookup: US Census Bureau.'
    }
