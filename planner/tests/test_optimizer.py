import random
from decimal import Decimal
from django.test import SimpleTestCase
from planner.errors import PlanningError
from planner.optimizer import Stop, optimize


class OptimizerTests(SimpleTestCase):
    def test_short_trip_uses_owned_fuel(self):
        p = optimize([], 240)
        self.assertEqual(p['total_fuel_cost'], '0.00')
        self.assertEqual(p['arrival_gallons'], 26)

    def test_exactly_500_miles(self):
        self.assertEqual(optimize([], 500)['arrival_gallons'], 0)

    def test_gap_is_infeasible(self):
        with self.assertRaises(PlanningError):
            optimize([], 500.01)

    def test_wait_for_cheaper_station(self):
        p = optimize([Stop(1, 400, Decimal(4)), Stop(2, 600, Decimal(2))], 1000)
        self.assertEqual([s['gallons'] for s in p['purchases']], [10, 40])
        self.assertEqual(p['total_fuel_cost'], '120.00')

    def test_fill_at_cheap_station(self):
        p = optimize([Stop(1, 300, Decimal(2)), Stop(2, 700, Decimal(5))], 900)
        self.assertEqual([s['gallons'] for s in p['purchases']], [30, 10])
        self.assertEqual(p['total_fuel_cost'], '110.00')

    def test_equal_position_prefers_cheapest(self):
        p = optimize([Stop(1, 0, Decimal(5)), Stop(2, 0, Decimal(2))], 100, 0)
        self.assertEqual(p['purchases'][0]['station_id'], 2)
        self.assertEqual(p['total_fuel_cost'], '20.00')

    def test_empty_initial_tank_requires_start_station(self):
        with self.assertRaises(PlanningError):
            optimize([Stop(1, 1, Decimal(3))], 100, 0)

    def test_multiple_full_tanks(self):
        p = optimize([Stop(i, i*450, Decimal(3)) for i in range(1, 6)], 2600)
        self.assertEqual(p['gallons_purchased'], 210)
        self.assertEqual(p['total_fuel_cost'], '630.00')

    def test_seeded_random_cases_against_independent_dynamic_program(self):
        """Integer-gallon cases have integer optima; DP exhausts all buy amounts."""
        rng = random.Random(73)
        for case in range(160):
            count = rng.randint(1, 9)
            positions = [0]
            for _ in range(count):
                positions.append(positions[-1] + rng.randint(1, 50))
            prices = [rng.randint(1, 7) for _ in range(count)]
            initial = rng.randint(0, 50)
            states = {initial: 0}
            for i in range(count):
                distance = positions[i+1]-positions[i]
                next_states = {}
                for fuel, cost in states.items():
                    for buy in range(max(0, distance-fuel), 51-fuel):
                        remaining = fuel+buy-distance
                        value = cost+buy*prices[i]
                        next_states[remaining] = min(next_states.get(remaining, float('inf')), value)
                states = next_states
            expected = min(states.values())
            stops = [Stop(i, positions[i]*10, Decimal(prices[i])) for i in range(count)]
            actual = optimize(stops, positions[-1]*10, initial)
            self.assertEqual(Decimal(actual['total_fuel_cost']), expected, f'case {case}')
            for purchase in actual['purchases']:
                self.assertLessEqual(purchase['departure_gallons'], 50+1e-7)
                self.assertGreaterEqual(purchase['arrival_gallons'], 0)
            self.assertAlmostEqual(initial+actual['gallons_purchased']-actual['gallons_consumed'], actual['arrival_gallons'])
