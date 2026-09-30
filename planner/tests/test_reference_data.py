import csv
import hashlib
import json

from django.conf import settings
from django.test import SimpleTestCase
from shapely.geometry import box

from planner.geometry import validate_coordinate
from scripts.build_reference_data import match_loves, match_pilot, match_ta


class ReferenceDataTests(SimpleTestCase):
    def test_loves_requires_unique_operator_identity_and_us_coordinate(self):
        row = {'OPIS Truckstop ID': '3', 'Truckstop Name': 'LOVES #005', 'City': 'Kingfisher', 'State': 'OK'}
        location = {'number': 5, 'city': 'Kingfisher', 'state': 'OK', 'isLoveStore': True,
                    'latitude': 35.85947, 'longitude': -97.932291, 'name': 'Site 5', 'address1': '203 S Main St'}
        boundary = box(-130, 20, -60, 50)
        self.assertEqual(match_loves([row], [location], boundary)[3]['latitude'], 35.85947)
        for field, value in [('State', 'TX'), ('City', 'Other'), ('Truckstop Name', 'OTHER #005'),
                             ('Truckstop Name', 'LOVES #6')]:
            self.assertEqual(match_loves([{**row, field: value}], [location], boundary), {})
        self.assertEqual(match_loves([row], [location, location], boundary), {})
        self.assertEqual(match_loves([row], [{**location, 'isLoveStore': False}], boundary), {})
        self.assertEqual(match_loves([row], [{**location, 'latitude': 0}], boundary), {})

    def test_bundled_stations_are_unique_us_locations_from_price_file(self):
        with (settings.BASE_DIR / 'data/fuel-prices.csv').open(newline='') as file:
            prices = {int(row['OPIS Truckstop ID']): row for row in csv.DictReader(file)}
        coordinates = json.loads((settings.BASE_DIR / 'data/verified-coordinates.json').read_text())
        self.assertGreaterEqual(len(coordinates), 600)
        self.assertEqual(len(coordinates), len({row['opis_id'] for row in coordinates}))
        states = set()
        for row in coordinates:
            self.assertIn(row['opis_id'], prices)
            self.assertTrue(row['source'].startswith('https://'))
            validate_coordinate({'lat': row['latitude'], 'lon': row['longitude']})
            states.add(prices[row['opis_id']]['State'])
        self.assertGreaterEqual(len(states), 40)

    def test_original_price_file_is_unchanged(self):
        self.assertEqual(hashlib.sha256((settings.BASE_DIR / 'data/fuel-prices.csv').read_bytes()).hexdigest(),
                         'c704371f141ded9c54df6c32d488a0ba2ceb589f88c936c967daa5330e0cd241')

    def test_pilot_match_requires_identity_and_uses_station_not_city_coordinate(self):
        row = {'OPIS Truckstop ID':'1', 'Truckstop Name':'PILOT #30', 'City':'Greenfield', 'State':'IN'}
        profile = {'address':{'countryCode':'US', 'city':'Greenfield', 'region':'IN', 'line1':'2640 N 600 W'},
                   'name':'Pilot Travel Center', 'c_liveOnPages':True, 'c_externalStoreNumber':'30',
                   'yextRoutableCoordinate':{'lat':39.82, 'long':-85.91},
                   'cityCoordinate':{'lat':39.82, 'long':-85.77}, 'c_pagesURL':'https://locations.pilotflyingj.com/example'}
        boundary = box(-130, 20, -60, 50)
        result = match_pilot([row], [profile], boundary)
        self.assertEqual(result[1]['longitude'], -85.91)
        for field, value in [('State','OH'), ('City','Other Town'), ('Truckstop Name','PILOT #31')]:
            self.assertEqual(match_pilot([{**row, field:value}], [profile], boundary), {})
        self.assertEqual(match_pilot([row], [profile, profile], boundary), {})

    def test_ta_match_requires_corroboration_and_rejects_ambiguity(self):
        row = {'OPIS Truckstop ID':'2', 'Truckstop Name':'PETRO STOPPING CENTER #348', 'City':'Shorter',
               'State':'AL', 'Address':'I-85, EXIT 22 & CR-138'}
        location = {'Location':'Petro Shorter','City':'Shorter','State':'Alabama','Site ID':'0505',
                    'Directions':'I-85, Exit 22', 'Latitude':'32.4056','Longitude':'-85.9556','Address':'428 Main St'}
        boundary = box(-130,20,-60,50)
        self.assertIn(2, match_ta([row], [location], boundary, {'alabama':'AL'}))
        self.assertEqual(match_ta([{**row,'Address':'I-85, EXIT 30'}], [location], boundary, {'alabama':'AL'}), {})
        self.assertEqual(match_ta([row], [location,location], boundary, {'alabama':'AL'}), {})
