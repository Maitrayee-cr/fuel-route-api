import json
import tempfile
from io import StringIO
from pathlib import Path
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from planner.models import Station


class ImportTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'stations.csv'
        self.path.write_text('OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n1,A,I40,City,TX,1,3.5\n1,A,I40,City,TX,2,3.9\n2,B,X,Toronto,ON,3,4.1\n')

    def test_dedup_filter_conservative_price_and_repeatability(self):
        for _ in range(2):
            call_command('import_stations', str(self.path), stdout=StringIO())
        self.assertEqual(Station.objects.count(), 1)
        self.assertEqual(str(Station.objects.get().price), '3.90000000')
        self.assertEqual(Station.objects.get().source_prices, ['3.5','3.9'])

    def test_bad_price_rolls_back_import(self):
        self.path.write_text(self.path.read_text()+'3,B,X,City,TX,4,NaN\n')
        with self.assertRaises(CommandError):
            call_command('import_stations', str(self.path), stdout=StringIO())
        self.assertEqual(Station.objects.count(), 0)

    def test_coordinate_import_validates_us_and_is_atomic(self):
        call_command('import_stations', str(self.path), stdout=StringIO())
        path = Path(self.temp.name)/'coords.json'
        path.write_text(json.dumps([{'opis_id':1,'latitude':35,'longitude':-100,'source':'https://example.com/station'}, {'opis_id':1,'latitude':43.65,'longitude':-79.38,'source':'https://example.com/not-us'}]))
        with self.assertRaises(CommandError):
            call_command('import_coordinates', str(path), stdout=StringIO())
        self.assertEqual(Station.objects.get().geocode_status, 'pending')
