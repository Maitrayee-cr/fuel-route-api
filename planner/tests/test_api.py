import json
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, override_settings
from planner.geometry import METERS_PER_MILE
from planner.models import Station, Trip
from planner.providers import Provider

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def route(total, legs):
    return {'distance': total*METERS_PER_MILE, 'duration': 10000,
            'geometry': {'type': 'LineString', 'coordinates': [[-100,35],[-98,35],[-95,35]]},
            'legs': [{'distance': d*METERS_PER_MILE} for d in legs]}


@override_settings(CACHES=CACHE)
class ApiTests(TestCase):
    def setUp(self):
        self.body = {'start': {'lat': 35, 'lon': -100}, 'finish': {'lat': 35, 'lon': -95}}
        self.a = Station.objects.create(opis_id=1, name='<script>alert(1)</script>', address='a', city='a', state='TX', price=4, latitude=35, longitude=-98, geocode_status='verified')
        self.b = Station.objects.create(opis_id=2, name='b', address='b', city='b', state='OK', price=3, latitude=35, longitude=-97, geocode_status='verified')

    def post(self, data=None):
        return self.client.post('/api/routes/', data if data is not None else self.body, content_type='application/json')

    def test_requires_json(self):
        self.assertEqual(self.client.post('/api/routes/', {}).status_code, 415)

    def test_invalid_json(self):
        self.assertEqual(self.client.post('/api/routes/', '{', content_type='application/json').status_code, 400)

    def test_bad_payloads(self):
        for payload in [[], {}, {'start': 1, 'finish': 2}, {**self.body, 'initial_fuel_gallons': 51}, {**self.body, 'initial_fuel_gallons': True}, {**self.body, 'initial_fuel_gallons': None}, {**self.body, 'initial_fuel_price': -1}, {**self.body, 'unknown': 1}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload).status_code, 400)

    def test_non_us_rejected_before_network(self):
        with patch('planner.providers.get_json') as call:
            response = self.post({**self.body, 'start': {'lat':43.65,'lon':-79.38}})
        self.assertEqual(response.json()['error']['code'], 'outside_usa')
        call.assert_not_called()

    def test_same_location(self):
        self.assertEqual(self.post({'start':self.body['start'], 'finish':self.body['start']}).status_code, 400)

    @patch('planner.service.route_candidates')
    @patch('planner.providers.Provider.route')
    def test_actual_detours_are_priced_and_map_persisted(self, routing, candidates):
        routing.side_effect = [route(1100, [1100]), route(1120, [460,460,200])]
        candidates.return_value = [(self.a, 450, 0), (self.b, 900, 0)]
        response = self.post()
        self.assertEqual(response.status_code, 201, response.content)
        result = response.json()
        self.assertEqual(result['total_fuel_cost'], '228.00')
        self.assertEqual(routing.call_count, 2)
        self.assertEqual(result['distance_miles'], 1120)
        self.assertEqual(sum(Decimal(p['cost']) for p in result['purchases']), Decimal(result['total_fuel_cost']))
        self.assertEqual(Trip.objects.count(), 1)
        page = self.client.get('/maps/'+result['id']+'/')
        self.assertContains(page, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(page, '<script>alert(1)</script>')
        self.assertEqual(self.client.get('/api/routes/'+result['id']+'/').status_code, 200)

    @patch('planner.service.route_candidates')
    @patch('planner.providers.Provider.route')
    def test_detour_range_failure_is_reported(self, routing, candidates):
        routing.side_effect = [route(1100, [1100]), route(1170, [510,460,200])]
        candidates.return_value = [(self.a, 450, 0), (self.b, 900, 0)]
        response = self.post()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['error']['code'], 'detour_exceeds_range')
        self.assertEqual(Trip.objects.count(), 0)

    @patch('planner.service.route_candidates', return_value=[])
    @patch('planner.providers.Provider.route', return_value=route(200, [200]))
    def test_short_trip_one_route_call_and_initial_value(self, routing, candidates):
        result = self.post({**self.body, 'initial_fuel_price': 3.5}).json()
        self.assertEqual(result['total_fuel_cost'], '0.00')
        self.assertEqual(result['total_cash_including_initial_fuel'], '175.00')
        self.assertEqual(routing.call_count, 1)

    @patch('planner.providers.get_json')
    def test_provider_cache_reuses_route(self, get):
        from django.core.cache import cache
        cache.clear()
        get.return_value = {'code': 'Ok', 'routes': [route(200, [200])]}
        provider = Provider()
        first = provider.route([(-100,35),(-95,35)])
        second = provider.route([(-100,35),(-95,35)])
        self.assertEqual(first, second)
        self.assertEqual(provider.routing_calls, 1)
        self.assertEqual(provider.cache_hits, 1)

    @patch('planner.providers.get_json', return_value={'code':'Ok','routes':[{'distance':1}]})
    def test_malformed_upstream_is_controlled(self, get):
        from django.core.cache import cache
        cache.clear()
        response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['error']['code'], 'upstream_error')

    @override_settings(GEOAPIFY_API_KEY='')
    def test_text_input_explains_missing_key(self):
        response = self.post({'start':'123 Example Avenue, Exampleville, TX','finish':'Austin, TX'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'geocoding_not_configured')

    @patch('planner.providers.get_json')
    def test_unloaded_data_fails_before_routing(self, get):
        Station.objects.all().delete()
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'data_not_loaded')
        get.assert_not_called()

    def test_large_payload_is_json_error(self):
        response = self.client.post('/api/routes/', '{"start":"' + 'x'*17000 + '"}', content_type='application/json')
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()['error']['code'], 'request_too_large')

    def test_missing_saved_trip_is_json_error(self):
        response = self.client.get('/api/routes/00000000-0000-0000-0000-000000000000/')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], 'route_not_found')

    @patch('planner.service.route_candidates', return_value=[])
    @patch('planner.providers.Provider.route', return_value=route(200, [200]))
    def test_consumed_fuel_cost_excludes_unused_initial_fuel(self, routing, candidates):
        result = self.post({**self.body, 'initial_fuel_price': 3.5}).json()
        self.assertEqual(result['fuel_accounting']['total_consumed_fuel_cost'], '70.00')
        self.assertEqual(result['fuel_accounting']['initial_gallons_consumed'], 20)
        self.assertEqual(result['total_fuel_cost'], '0.00')
        self.assertEqual(result['fuel_purchased_cost'], '0.00')
        self.assertEqual(result['fuel_consumed_gallons'], 20)
        self.assertEqual(result['total_trip_fuel_cost'], '70.00')
        page = self.client.get('/maps/' + result['id'] + '/')
        self.assertContains(page, 'Total trip fuel cost')
        self.assertContains(page, 'Cost-optimized among available verified stations.')

    @patch('planner.service.route_candidates', return_value=[])
    @patch('planner.providers.Provider.route', return_value=route(200, [200]))
    def test_missing_starting_price_does_not_invent_consumption_cost(self, routing, candidates):
        result = self.post().json()
        self.assertIsNone(result['fuel_accounting']['total_consumed_fuel_cost'])
        self.assertTrue(result['fuel_accounting']['initial_price_required'])
        self.assertIsNone(result['total_trip_fuel_cost'])
        self.assertEqual(result['fuel_purchased_cost'], '0.00')
        self.assertContains(self.client.get('/maps/' + result['id'] + '/'), '<strong>Unknown</strong>')

    @patch('planner.service.route_candidates', return_value=[])
    @patch('planner.providers.Provider.route', return_value=route(200, [200]))
    def test_explicit_zero_starting_price_is_not_unknown(self, routing, candidates):
        result = self.post({**self.body, 'initial_fuel_price': 0}).json()
        self.assertEqual(result['total_trip_fuel_cost'], '0.00')
        self.assertFalse(result['fuel_accounting']['initial_price_required'])
        self.assertNotContains(self.client.get('/maps/' + result['id'] + '/'), '<strong>Unknown</strong>')

    @patch('planner.service.route_candidates')
    @patch('planner.providers.Provider.route')
    def test_wider_corridor_reuses_base_route(self, routing, candidates):
        routing.side_effect = [route(800, [800]), route(810, [410,400])]
        candidates.return_value = [(self.a, 400, 4)]
        response = self.post()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['coverage']['corridor_miles'], 10)
        self.assertEqual(routing.call_count, 2)

    @patch('planner.service.route_candidates')
    @patch('planner.providers.Provider.route')
    def test_detour_replans_once_with_earlier_station(self, routing, candidates):
        c = Station.objects.create(opis_id=3, name='early', address='c', city='c', state='TX', price=4,
                                   latitude=35, longitude=-98.1, geocode_status='verified')
        self.a.price = Decimal(2)
        self.a.save()
        candidates.return_value = [(c, 480, 0), (self.a, 499, 0), (self.b, 900, 0)]
        routing.side_effect = [route(1100, [1100]), route(1102, [501,401,200]),
                               route(1110, [480,20,410,200])]
        response = self.post()
        self.assertEqual(response.status_code, 201, response.content)
        result = response.json()
        self.assertTrue(result['detour_replanned'])
        self.assertEqual(routing.call_count, 3)
        self.assertEqual([v['station_id'] for v in result['visits']], [3,1,2])
        self.assertTrue(all(p['arrival_gallons'] >= 0 and p['departure_gallons'] <= 50 for p in result['purchases']))

    @override_settings(GEOAPIFY_API_KEY='')
    @patch('planner.providers.get_json')
    def test_city_names_resolve_without_network(self, get):
        provider = Provider()
        for place in ['Dallas, TX', 'Austin, Texas, USA', 'Miami, FL', 'New York City, NY']:
            lon, lat = provider.location(place)
            self.assertTrue(-180 < lon < 180 and -90 < lat < 90)
        self.assertEqual(provider.local_geocodes, 4)
        self.assertEqual(provider.geocoding_calls, 0)
        get.assert_not_called()
