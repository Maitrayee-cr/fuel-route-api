from types import SimpleNamespace
from django.test import SimpleTestCase
from planner.errors import PlanningError
from planner.geometry import validate_coordinate, route_candidates, METERS_PER_MILE


class GeometryTests(SimpleTestCase):
    def test_usa_points(self):
        for lat, lon in [(34.05, -118.24), (40.71, -74.0), (21.31, -157.85), (61.21, -149.9),
                         (25.7617, -80.1918), (24.5551, -81.78), (42.3601, -71.0589), (47.6062, -122.3321)]:
            self.assertEqual(validate_coordinate({'lat': lat, 'lon': lon}), (lon, lat))

    def test_rejects_non_us_inside_na_bounding_box(self):
        for lat, lon in [(43.65, -79.38), (19.43, -99.13), (51.5, -0.12), (30, -60),
                         (42.3174, -83.0268), (32.5149, -117.0382), (49.2827, -123.1207)]:
            with self.assertRaises(PlanningError):
                validate_coordinate({'lat': lat, 'lon': lon})

    def test_rejects_invalid_coordinate_values(self):
        for value in [{'lat': True, 'lon': -100}, {'lat': float('nan'), 'lon': -100}, {'lat': 35}, [35, -100]]:
            with self.assertRaises(PlanningError):
                validate_coordinate(value)

    def test_projection_distance_is_along_route_not_from_origin(self):
        route = {'distance': 200*METERS_PER_MILE, 'geometry': {'coordinates': [[-100, 35], [-99, 35], [-99, 36]]}}
        close = SimpleNamespace(latitude=35.5, longitude=-99.001)
        far = SimpleNamespace(latitude=35.5, longitude=-98)
        found = route_candidates(route, [close, far], 2)
        self.assertEqual(len(found), 1)
        self.assertGreater(found[0][1], 100)
        self.assertLess(found[0][1], 200)
