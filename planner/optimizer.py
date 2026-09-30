"""Exact continuous-fuel optimizer on one ordered, fixed-distance route.

Exchange argument: if a cheaper station is reachable, buying beyond the fuel
needed to reach the first such station can only increase cost. Otherwise buy
as much as can be used before the destination, capped by tank capacity.
An O(n) monotonic stack finds the first strictly cheaper station ahead.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from .errors import PlanningError

CAPACITY = 50.0
MPG = 10.0
RANGE = CAPACITY * MPG
EPS = 1e-7


@dataclass(frozen=True)
class Stop:
    station_id: int
    mile: float
    price: Decimal


def money(value):
    return str(Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def optimize(stops, total_miles, initial_gallons=50.0, *, capacity=CAPACITY):
    if not 0 < capacity <= CAPACITY or not 0 <= initial_gallons <= capacity or total_miles < 0:
        raise ValueError('Invalid fuel or distance')
    stops = sorted(stops, key=lambda s: (s.mile, s.price, s.station_id))
    if any(s.mile < 0 or s.mile > total_miles or s.price <= 0 for s in stops):
        raise ValueError('Invalid station')
    next_cheaper = [None] * len(stops)
    stack = []
    for i in range(len(stops) - 1, -1, -1):
        while stack and stops[stack[-1]].price >= stops[i].price:
            stack.pop()
        if stack:
            next_cheaper[i] = stack[-1]
        stack.append(i)
    fuel, previous = float(initial_gallons), 0.0
    purchases = []
    total_cost = Decimal(0)
    for i, stop in enumerate(stops):
        fuel -= (stop.mile - previous) / MPG
        if fuel < -EPS:
            raise PlanningError('insufficient_coverage', 'No feasible fuel plan with the available stations and initial fuel.')
        fuel = max(0.0, fuel)
        arrival = fuel
        target = min(capacity * MPG, total_miles - stop.mile)
        j = next_cheaper[i]
        if j is not None and stops[j].mile - stop.mile <= capacity * MPG:
            target = min(target, stops[j].mile - stop.mile)
        buy = max(0.0, target / MPG - fuel)
        if buy > EPS:
            # Retain full quantity precision; round each actual transaction to cents.
            cost = (Decimal(str(buy)) * stop.price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            total_cost += cost
            purchases.append({'station_id': stop.station_id, 'mile': stop.mile,
                              'gallons': buy, 'price_per_gallon': str(stop.price),
                              'cost': str(cost), 'arrival_gallons': arrival,
                              'departure_gallons': fuel + buy})
            fuel += buy
        previous = stop.mile
    fuel -= (total_miles - previous) / MPG
    if fuel < -EPS:
        raise PlanningError('insufficient_coverage', 'Destination cannot be reached within the 500-mile range using available stations.')
    return {'purchases': purchases, 'total_fuel_cost': money(total_cost),
            'gallons_purchased': sum(p['gallons'] for p in purchases),
            'gallons_consumed': total_miles / MPG, 'arrival_gallons': max(0.0, fuel)}
