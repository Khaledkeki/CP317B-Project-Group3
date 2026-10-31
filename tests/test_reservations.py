"""Reservation, availability, and concurrency checks using isolated databases."""

from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
import secrets
import tempfile
import threading
import unittest
from urllib.parse import urlencode

from fastapi.testclient import TestClient
from app import auth, fleet, reservations
from app.database import get_connection
from app.main import create_app


class HiddenInputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('type') == 'hidden' and attrs.get('name'):
            self.values[attrs['name']] = attrs.get('value', '')


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'rentals.db'
        self.key = 'reservation-tests-secret-' * 3
        self.client = TestClient(create_app(self.database, self.key), follow_redirects=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.customer = auth.register_user(self.database, 'Customer One', 'one@example.test', 'customer1', 'Password123!')
        self.other = auth.register_user(self.database, 'Customer Two', 'two@example.test', 'customer2', 'Password123!')
        self.staff = auth.register_user(self.database, 'Staff', 'staff@example.test', 'staff', 'Password123!', role='staff')
        self.values = {'fleet_code': 'CAR-001', 'name': 'Toyota Corolla', 'branch': 'Waterloo',
                       'vehicle_type': 'sedan', 'category': 'economy', 'seats': '5', 'transmission': 'automatic',
                       'fuel_type': 'gasoline', 'daily_rate': '50.00', 'status': 'available'}
        self.vehicle = fleet.save_vehicle(self.database, self.values)
        self.pickup = '2099-07-01T09:00'
        self.return_time = '2099-07-02T10:00'
        self.params = {'vehicle_id': self.vehicle.id, 'branch': 'Waterloo', 'pickup': self.pickup,
                       'return_time': self.return_time, 'vehicle_type': 'sedan'}
        self.as_user(self.customer)

    def as_user(self, user):
        self.client.cookies.set(auth.SESSION_COOKIE, auth.create_session(self.database, self.key, user))

    def review_form(self):
        response = self.client.get('/reservations/review', params=self.params)
        self.assertEqual(response.status_code, 200)
        parser = HiddenInputs()
        parser.feed(response.text)
        return response, parser.values

    def confirm(self, form):
        return self.client.post('/reservations/confirm', data=form)

    def test_booking_journey_pricing_confirmation_account_and_search(self):
        review, form = self.review_form()
        self.assertIn('Toyota Corolla', review.text)
        self.assertIn('$100.00', review.text)
        self.assertIn('<dd>2</dd>', review.text)
        response = self.confirm(form)
        self.assertEqual(response.status_code, 303)
        location = response.headers['location']
        receipt = self.client.get(location)
        self.assertEqual(receipt.status_code, 200)
        self.assertIn('Reservation confirmed', receipt.text)
        self.assertIn('$100.00', receipt.text)
        reference = location.rsplit('/', 1)[-1]
        self.assertIn(reference, self.client.get('/account').text)
        search = self.client.get('/search', params={k: self.params[k] for k in ['branch','pickup','return_time']})
        self.assertNotIn('Toyota Corolla', search.text)
        self.assertIn('No cars match your search', search.text)

    def test_pricing_for_partial_and_exact_periods(self):
        for end, days in [('2099-07-01T09:01', 1), ('2099-07-02T09:00', 1),
                          ('2099-07-02T09:01', 2), ('2099-07-03T09:00', 2)]:
            with self.subTest(end=end):
                quote = reservations.get_quote(self.database, self.vehicle.id, 'Waterloo', self.pickup, end)
                self.assertEqual((quote.days, quote.total_cents), (days, days * 5000))

    def test_overlap_and_adjacent_intervals(self):
        _, form = self.review_form()
        self.confirm(form)
        for start, end, available in [
            ('2099-07-01T08:00', '2099-07-01T09:00', True),
            ('2099-07-02T10:00', '2099-07-02T11:00', True),
            ('2099-07-01T08:00', '2099-07-01T09:01', False),
            ('2099-07-02T09:59', '2099-07-02T11:00', False),
            ('2099-07-01T10:00', '2099-07-01T11:00', False),
            ('2099-07-01T08:00', '2099-07-02T11:00', False),
        ]:
            with self.subTest(start=start, end=end):
                response = self.client.get('/search', params={'branch':'Waterloo', 'pickup':start, 'return_time':end})
                self.assertEqual('Toyota Corolla' in response.text, available)
                review = self.client.get('/reservations/review', params={**self.params, 'pickup':start, 'return_time':end})
                self.assertEqual(review.status_code, 200 if available else 409)

    def test_duplicate_submission_returns_same_reference_without_duplicate_booking(self):
        _, form = self.review_form()
        first = self.confirm(form)
        second = self.confirm(form)
        self.assertEqual(first.headers['location'], second.headers['location'])
        with get_connection(self.database) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM reservations').fetchone()[0], 1)
        modified = self.confirm({**form, 'return_time':'2099-07-03T10:00'})
        self.assertEqual(modified.status_code, 409)

    def test_two_customers_cannot_book_same_car_concurrently(self):
        barrier = threading.Barrier(2)
        def attempt(user_id):
            barrier.wait(timeout=10)
            try:
                return reservations.create_reservation(self.database, user_id, self.vehicle.id, 'Waterloo',
                    self.pickup, self.return_time, str(self.vehicle.version), secrets.token_urlsafe(32))
            except reservations.ReservationError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, [self.customer.id, self.other.id]))
        self.assertEqual(sum(result is not None for result in outcomes), 1)
        with get_connection(self.database) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM reservations').fetchone()[0], 1)

    def test_price_and_vehicle_changes_require_new_review(self):
        _, form = self.review_form()
        fleet.save_vehicle(self.database, {**self.values, 'daily_rate':'80.00', 'version':self.vehicle.version}, self.vehicle.id)
        response = self.confirm(form)
        self.assertEqual(response.status_code, 409)
        self.assertIn('rate changed', response.text)
        with get_connection(self.database) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM reservations').fetchone()[0], 0)

    def test_saved_price_and_details_survive_fleet_changes_and_removal(self):
        _, form = self.review_form()
        reference = self.confirm(form).headers['location'].rsplit('/', 1)[-1]
        updated = fleet.save_vehicle(self.database, {**self.values, 'name':'Changed', 'branch':'Toronto',
            'daily_rate':'90', 'version':self.vehicle.version}, self.vehicle.id)
        fleet.retire_vehicle(self.database, updated.id, str(updated.version))
        record = reservations.get_reservation(self.database, self.customer.id, reference)
        self.assertEqual((record['vehicle_name'], record['branch'], record['daily_rate_cents'], record['total_cents']),
                         ('Toyota Corolla','Waterloo',5000,10000))
        self.assertEqual(self.client.get('/reservations/'+reference).status_code, 200)

    def test_receipt_is_private_and_csrf_is_required(self):
        _, form = self.review_form()
        self.assertEqual(self.confirm({**form, 'csrf_token':'bad'}).status_code, 403)
        first = self.confirm(form)
        self.as_user(self.other)
        self.assertEqual(self.client.get(first.headers['location']).status_code, 404)
        self.assertNotIn('Toyota Corolla', self.client.get('/account').text)
        self.assertEqual(self.confirm(form).status_code, 409)

    def test_staff_cannot_confirm_and_anonymous_user_is_sent_to_login(self):
        self.client.cookies.clear()
        response = self.client.get('/reservations/review', params=self.params)
        self.assertIn('Sign in to confirm', response.text)
        self.assertIn('/login?next=', response.text)
        self.as_user(self.staff)
        response = self.client.get('/reservations/review', params=self.params)
        self.assertIn('sign in with a customer account', response.text)
        parser = HiddenInputs(); parser.feed(response.text)
        self.assertEqual(self.client.post('/reservations/confirm', data={**self.params, 'csrf_token':parser.values['csrf_token']}).status_code, 403)

    def test_login_preserves_safe_review_destination_and_rejects_external_redirects(self):
        self.client.cookies.clear()
        destination = '/reservations/review?' + urlencode(self.params)
        page = self.client.get('/login', params={'next':destination})
        parser = HiddenInputs(); parser.feed(page.text)
        result = self.client.post('/login', data={**parser.values, 'identifier':'customer1', 'password':'Password123!'})
        self.assertEqual(result.headers['location'], destination)
        self.assertEqual(self.client.get(destination).status_code, 200)
        self.client.cookies.clear()
        page = self.client.get('/login', params={'next':'https://example.com'})
        parser = HiddenInputs(); parser.feed(page.text)
        result = self.client.post('/login', data={**parser.values, 'identifier':'customer1', 'password':'Password123!', 'next':'//evil.test'})
        self.assertEqual(result.headers['location'], '/account')

    def test_server_ignores_client_price_and_checks_branch_and_status(self):
        _, form = self.review_form()
        self.assertEqual(self.confirm({**form, 'branch':'Toronto'}).status_code, 409)
        response = self.confirm({**form, 'total_cents':'1', 'daily_rate_cents':'1'})
        record = reservations.get_reservation(self.database, self.customer.id, response.headers['location'].rsplit('/',1)[-1])
        self.assertEqual(record['total_cents'], 10000)

    def test_invalid_dates_and_stale_availability_are_explained(self):
        response = self.client.get('/reservations/review', params={**self.params,'return_time':self.pickup})
        self.assertEqual(response.status_code, 409)
        self.assertIn('Return must be after pickup', response.text)
        _, first = self.review_form()
        _, second = self.review_form()
        self.confirm(first)
        response = self.confirm(second)
        self.assertEqual(response.status_code, 409)
        self.assertIn('no longer available', response.text)
        self.assertIn('Back to results', response.text)


if __name__ == '__main__':
    unittest.main()
