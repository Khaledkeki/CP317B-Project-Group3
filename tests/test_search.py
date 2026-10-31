"""Customer search tests use a temporary fleet and no real customer data."""

from pathlib import Path
from datetime import datetime, timedelta
import tempfile
import unittest

from fastapi.testclient import TestClient
from app import fleet
from app.main import create_app


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "search.db"
        self.client = TestClient(create_app(self.database, "search-test-secret-" * 4), follow_redirects=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.values = {"fleet_code": "CAR-001", "name": "Toyota Corolla", "branch": "Waterloo",
                       "vehicle_type": "sedan", "category": "economy", "seats": "5", "transmission": "automatic",
                       "fuel_type": "gasoline", "daily_rate": "49.99", "status": "available"}
        self.add()
        self.add(fleet_code="CAR-002", name="Honda CR-V", vehicle_type="suv", category="standard",
                 seats="7", fuel_type="hybrid", daily_rate="79.95")
        self.add(fleet_code="CAR-003", name="Unavailable car", status="rented", branch="Toronto")
        self.add(fleet_code="CAR-004", name="Repair car", status="under_repair")
        retired = self.add(fleet_code="CAR-005", name="Removed car")
        fleet.retire_vehicle(self.database, retired.id, str(retired.version))
        self.query = {"branch": "Waterloo", "pickup": "2099-07-01T09:00", "return_time": "2099-07-02T09:00"}

    def add(self, **changes):
        return fleet.save_vehicle(self.database, {**self.values, **changes})

    def search(self, **changes):
        return self.client.get('/search', params={**self.query, **changes})

    def test_get_search_and_matching_available_cars(self):
        self.assertEqual(self.client.get('/search').status_code, 200)
        response = self.search()
        self.assertEqual(response.status_code, 200)
        self.assertIn('2 cars found', response.text)
        self.assertIn('Toyota Corolla', response.text)
        self.assertIn('Honda CR-V', response.text)
        self.assertIn('$49.99', response.text)
        self.assertNotIn('Unavailable car', response.text)
        self.assertNotIn('Repair car', response.text)
        self.assertNotIn('Removed car', response.text)
        self.assertIn('Results show cars available for the selected dates', response.text)
        self.assertIn('Select car', response.text)

    def test_individual_and_combined_filters(self):
        for filter_values, expected, excluded in [
            ({'vehicle_type': 'sedan'}, 'Toyota Corolla', 'Honda CR-V'),
            ({'category': 'standard'}, 'Honda CR-V', 'Toyota Corolla'),
            ({'max_price': '50.00'}, 'Toyota Corolla', 'Honda CR-V'),
            ({'seats': '6'}, 'Honda CR-V', 'Toyota Corolla'),
            ({'fuel_type': 'hybrid'}, 'Honda CR-V', 'Toyota Corolla'),
            ({'transmission': 'manual'}, None, 'Toyota Corolla'),
            ({'vehicle_type': 'suv', 'category': 'standard', 'seats': '7', 'max_price': '79.95',
              'fuel_type': 'hybrid', 'transmission': 'automatic'}, 'Honda CR-V', 'Toyota Corolla'),
        ]:
            with self.subTest(filters=filter_values):
                response = self.search(**filter_values)
                self.assertEqual(response.status_code, 200)
                if expected:
                    self.assertIn(expected, response.text)
                self.assertNotIn(excluded, response.text)

    def test_date_and_required_field_errors(self):
        for changed, field in [({'branch': ''}, 'branch'), ({'pickup': ''}, 'pickup'),
                               ({'pickup': 'invalid'}, 'pickup'), ({'return_time': '2099-02-30T09:00'}, 'return_time'),
                               ({'return_time': '2099-07-01T09:00'}, 'return_time'),
                               ({'return_time': '2099-06-30T09:00'}, 'return_time')]:
            with self.subTest(changed=changed):
                response = self.search(**changed)
                self.assertEqual(response.status_code, 400)
                self.assertIn(f'id="{field}-error"', response.text)
                self.assertIn('aria-invalid="true"', response.text)
                self.assertNotIn('Toyota Corolla', response.text)

    def test_past_pickup_rejected_future_pickup_accepted(self):
        now = datetime.now().replace(second=0, microsecond=0)
        past = (now - timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M')
        future = (now + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        later = (now + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        response = self.search(pickup=past, return_time=later)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Pickup date and time cannot be in the past.', response.text)
        self.assertIn('aria-describedby="pickup-error"', response.text)
        self.assertNotIn('Toyota Corolla', response.text)
        self.assertIn('Toyota Corolla', self.search(pickup=future, return_time=later).text)

    def test_invalid_filters_are_rejected_without_errors(self):
        for values in [{'seats': '0'}, {'seats': '16'}, {'seats': '1.2'}, {'max_price': '-1'},
                       {'max_price': 'NaN'}, {'max_price': '1.234'}, {'vehicle_type': 'unknown'},
                       {'category': 'unknown'}, {'transmission': 'unknown'}, {'fuel_type': 'unknown'}]:
            with self.subTest(filters=values):
                self.assertEqual(self.search(**values).status_code, 400)

    def test_branches_with_no_available_cars_show_empty_state(self):
        initial = self.client.get('/search')
        self.assertIn('Toronto', initial.text)
        response = self.search(branch='Toronto')
        self.assertEqual(response.status_code, 200)
        self.assertIn('No cars match your search', response.text)
        self.assertNotIn('Unavailable car', response.text)

    def test_rate_boundary_and_minimum_seats(self):
        self.assertIn('Toyota Corolla', self.search(max_price='49.99', seats='5').text)
        self.assertNotIn('Toyota Corolla', self.search(max_price='49.98').text)
        self.assertIn('Honda CR-V', self.search(seats='6').text)

    def test_search_is_available_without_javascript_and_escapes_vehicle_names(self):
        self.add(fleet_code='CAR-006', name='<script>alert(1)</script>')
        response = self.search()
        self.assertIn('&lt;script&gt;', response.text)
        self.assertNotIn('<script>alert(1)</script>', response.text)
        self.assertIn('method="get" action="/search"', response.text)
        self.assertEqual(self.client.get('/static/js/search.js').status_code, 200)

    def test_user_input_cannot_bypass_branch_filter(self):
        response = self.search(branch="Waterloo' OR 1=1 --")
        self.assertIn('No cars match your search', response.text)
        self.assertNotIn('Toyota Corolla', response.text)


if __name__ == '__main__':
    unittest.main()
