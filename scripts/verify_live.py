"""Opt-in live integration checks; use a prepared database and internet access.

python scripts/verify_live.py --output docs/verification.json
Never run this against a production database: it creates saved test trips.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()
from django.test import Client
from planner.models import Station

CASES = [
    ('Los Angeles to Oklahoma City', {'lat': 34.0522, 'lon': -118.2437}, {'lat': 35.4676, 'lon': -97.5164}),
    ('Chicago to Atlanta', {'lat': 41.8781, 'lon': -87.6298}, {'lat': 33.749, 'lon': -84.388}),
    ('New York to Miami', {'lat': 40.7128, 'lon': -74.006}, {'lat': 25.7617, 'lon': -80.1918}),
    ('Seattle to Denver', {'lat': 47.6062, 'lon': -122.3321}, {'lat': 39.7392, 'lon': -104.9903}),
    ('Dallas to Austin (local city names)', 'Dallas, TX', 'Austin, Texas, USA'),
    ('Boston to Nashville', {'lat': 42.3601, 'lon': -71.0589}, {'lat': 36.1627, 'lon': -86.7816}),
    ('New York to Los Angeles', {'lat': 40.7128, 'lon': -74.006}, {'lat': 34.0522, 'lon': -118.2437}),
    ('Chicago to Atlanta (local city names)', 'Chicago, IL', 'Atlanta, Georgia'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--example-output', type=Path)
    parser.add_argument('--case', action='append', type=int, help='Zero-based case index; default: all')
    args = parser.parse_args()
    results, client, passed = [], Client(), True
    for index, (name, start, finish) in enumerate(CASES):
        if args.case is not None and index not in args.case:
            continue
        payload = {'start': start, 'finish': finish, 'initial_fuel_price': 3.5}
        for phase in ('first', 'repeat'):
            begin = time.perf_counter()
            response = client.post('/api/routes/', payload, content_type='application/json')
            elapsed = time.perf_counter() - begin
            result = response.json()
            entry = {'case': name, 'phase': phase, 'status': response.status_code, 'seconds': round(elapsed, 4)}
            if response.status_code != 201:
                entry['error'] = result
                passed = False
            else:
                checks = {
                    'explicit_cost_fields': result['fuel_purchased_cost'] == result['total_fuel_cost'] and result['fuel_consumed_gallons'] == result['gallons_consumed'] and result['total_trip_fuel_cost'] == result['fuel_accounting']['total_consumed_fuel_cost'] and result['total_trip_fuel_cost'] is not None,
                    'cash_reconciles': sum(Decimal(p['cost']) for p in result['purchases']) == Decimal(result['total_fuel_cost']),
                    'fuel_reconciles': abs(result['initial_fuel_gallons'] + result['gallons_purchased'] - result['gallons_consumed'] - result['arrival_gallons']) < 1e-6,
                    'range_respected': all(p['arrival_gallons'] >= 0 and p['departure_gallons'] <= 50 + 1e-7 for p in result['purchases']) and all(leg['distance_miles'] <= 500 + 1e-6 for leg in result['legs']),
                    'routing_budget': result['external_calls']['routing'] <= 3,
                    'map_served': client.get('/maps/' + result['id'] + '/').status_code == 200,
                    'detail_served': client.get('/api/routes/' + result['id'] + '/').status_code == 200,
                }
                passed = passed and all(checks.values())
                entry.update({key: result[key] for key in ('distance_miles', 'total_fuel_cost', 'external_calls', 'local_geocodes', 'detour_replanned', 'coverage')})
                entry.update(stops=len(result['purchases']), checks=checks)
                if index == 0 and phase == 'first' and args.example_output:
                    args.example_output.write_text(json.dumps(result, indent=2) + '\n')
            results.append(entry)
            print(json.dumps(entry), flush=True)
            if response.status_code != 201:
                break
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'django': django.get_version(),
              'stations': Station.objects.count(), 'verified_stations': Station.objects.filter(geocode_status='verified').count(),
              'all_live_checks_passed': passed, 'results': results,
              'notes': 'First requests may already have cached geometry; external_calls states the actual network count. Timings are individual observations.'}
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
