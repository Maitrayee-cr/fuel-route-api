from django.test import SimpleTestCase

from planner.errors import PlanningError
from planner.locations import local_place


class LocationTests(SimpleTestCase):
    def test_state_names_and_punctuation(self):
        self.assertEqual(local_place('Dallas, TX'), local_place('dallas texas, United States'))
        self.assertEqual(local_place('St. Louis, MO'), local_place('Saint Louis, Missouri'))

    def test_ambiguous_city_requires_state(self):
        with self.assertRaises(PlanningError) as error:
            local_place('Springfield')
        self.assertEqual(error.exception.code, 'ambiguous_location')
        self.assertIsNotNone(local_place('Springfield, IL'))

    def test_address_and_foreign_city_are_not_guessed(self):
        self.assertIsNone(local_place('123 Main Street, Dallas, TX'))
        self.assertIsNone(local_place('Toronto, Ontario'))
        self.assertIsNone(local_place('Paris, France'))
