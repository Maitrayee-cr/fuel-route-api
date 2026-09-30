from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from django.core.cache import cache
from django.test import TestCase, override_settings

from planner.errors import PlanningError
from planner.models import ProviderGate
from planner.providers import Provider, get_json, reserve_request
from planner.tests.test_api import route


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ProviderTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch('planner.providers.reserve_request')
    @patch('planner.providers.urlopen')
    def test_timeout_and_http_errors_are_controlled_without_leaking_key(self, open_url, gate):
        for error, status in [(URLError('network unavailable'), 502), (TimeoutError(), 502),
                              (HTTPError('https://example.test/?apiKey=secret', 429, 'limited', {}, None), 503)]:
            open_url.side_effect = error
            with self.assertRaises(PlanningError) as caught:
                get_json('https://example.test/?apiKey=secret', 'geocoding')
            self.assertEqual(caught.exception.status, status)
            self.assertNotIn('secret', caught.exception.message)

    @patch('planner.providers.reserve_request')
    @patch('planner.providers.urlopen')
    def test_invalid_upstream_json_is_controlled(self, open_url, gate):
        response = MagicMock()
        response.read.return_value = b'<html>Gateway failure</html>'
        open_url.return_value.__enter__.return_value = response
        with self.assertRaises(PlanningError) as caught:
            get_json('https://example.test/route', 'routing')
        self.assertEqual(caught.exception.status, 502)

    @patch('planner.providers.get_json')
    def test_invalid_geometry_is_rejected_not_cached(self, get):
        for coordinates in [[[False,35],[-98,35]], [[-200,35],[-98,35]], [[-100,95],[-98,35]], [None,[-98,35]]]:
            data = route(200, [200])
            data['geometry']['coordinates'] = coordinates
            get.return_value = {'code':'Ok', 'routes':[data]}
            with self.assertRaises(PlanningError) as caught:
                Provider().route([(-100,35),(-98,35)])
            self.assertEqual(caught.exception.status, 502)
        self.assertEqual(get.call_count, 4)

    @patch('planner.providers.time.sleep')
    @patch('planner.providers.time.monotonic', side_effect=[0, 0, 4])
    @patch('planner.providers.time.time', return_value=100)
    def test_busy_provider_queue_is_bounded(self, wall_time, monotonic, sleep):
        ProviderGate.objects.create(provider='routing', next_allowed=1000)
        with self.assertRaises(PlanningError) as caught:
            reserve_request('routing')
        self.assertEqual(caught.exception.code, 'provider_busy')
        self.assertEqual(caught.exception.status, 503)
        sleep.assert_called_once_with(0.1)

    @patch('planner.providers.get_json')
    def test_route_cache_is_shared_across_provider_instances(self, get):
        get.return_value = {'code':'Ok', 'routes':[route(200, [200])]}
        first = Provider()
        first.route([(-100,35),(-98,35)])
        second = Provider()
        second.route([(-100,35),(-98,35)])
        self.assertEqual((first.routing_calls, second.routing_calls, second.cache_hits), (1,0,1))
        self.assertEqual(get.call_count, 1)
