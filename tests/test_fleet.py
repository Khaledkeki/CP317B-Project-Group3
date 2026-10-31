"""ADMIN-01 integration tests against temporary SQLite databases."""

from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from app import auth, fleet
from app.database import get_connection, initialize_database
from app.main import create_app


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "fleet.db"
        self.key = "fleet-test-key-" * 5
        self.client = TestClient(create_app(self.database, self.key), follow_redirects=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.staff = auth.register_user(self.database, "Test Staff", "staff@example.test", "staff",
                                        "TestPassword123!", role="staff")
        self.client.cookies.set(auth.SESSION_COOKIE, auth.create_session(self.database, self.key, self.staff))
        self.client.get("/staff/fleet")
        self.values = {"fleet_code": "CAR-001", "name": "Toyota Corolla", "branch": "Waterloo",
                       "vehicle_type": "sedan", "category": "economy", "seats": "5",
                       "transmission": "automatic", "fuel_type": "gasoline", "daily_rate": "49.99",
                       "status": "available"}

    def post(self, path, values=None):
        return self.client.post(path, data={"csrf_token": self.client.cookies[auth.CSRF_COOKIE],
                                            **(self.values if values is None else values)})

    def add_vehicle(self, **changes):
        return fleet.save_vehicle(self.database, {**self.values, **changes})

    def test_staff_can_create_a_persistent_vehicle_with_exact_cents(self):
        response = self.post("/staff/vehicles/new")
        self.assertEqual(response.status_code, 303)
        vehicle = fleet.list_vehicles(self.database)[0]
        self.assertEqual(vehicle.daily_rate_cents, 4999)
        self.assertEqual(vehicle.daily_rate, "49.99")
        self.assertEqual(vehicle.branch, "Waterloo")
        self.assertEqual(vehicle.seats, 5)
        initialize_database(self.database)
        self.assertEqual(fleet.get_vehicle(self.database, vehicle.id), vehicle)
        self.assertIn("Toyota Corolla", self.client.get("/staff/fleet").text)

    def test_all_mutations_and_forms_are_staff_only(self):
        vehicle = self.add_vehicle()
        routes = ["/staff/fleet", "/staff/vehicles/new", f"/staff/vehicles/{vehicle.id}/edit",
                  f"/staff/vehicles/{vehicle.id}/remove"]
        with get_connection(self.database) as connection:
            connection.execute("UPDATE users SET role='customer' WHERE id=?", (self.staff.id,))
        for path in routes:
            self.assertEqual(self.client.get(path).status_code, 403, path)
        for path in routes[1:]:
            self.assertEqual(self.post(path).status_code, 403, path)
        self.client.cookies.clear()
        for path in routes:
            self.assertEqual(self.client.get(path).status_code, 401, path)
        for path in routes[1:]:
            self.assertEqual(self.client.post(path, data=self.values).status_code, 401, path)
        self.assertEqual(len(fleet.list_vehicles(self.database)), 1)
        self.assertEqual(fleet.get_vehicle(self.database, vehicle.id), vehicle)

    def test_missing_fields_and_unsupported_options_have_linked_errors(self):
        response = self.post("/staff/vehicles/new", {})
        self.assertEqual(response.status_code, 400)
        for field in ["fleet_code", "name", "branch", "vehicle_type", "category", "seats",
                      "transmission", "fuel_type", "daily_rate", "status"]:
            self.assertIn(f'id="{field}-error"', response.text)
        self.assertEqual(fleet.list_vehicles(self.database), [])
        response = self.post("/staff/vehicles/new", {**self.values, "status": "fake", "fuel_type": "unknown"})
        self.assertEqual(response.status_code, 400)
        self.assertIn('id="status-error"', response.text)
        self.assertIn('id="fuel_type-error"', response.text)

    def test_invalid_rates_and_seats_do_not_create_records(self):
        for value in ["-1", "0", "1.999", "NaN", "Infinity", "1e2", "10000", "abc"]:
            response = self.post("/staff/vehicles/new", {**self.values, "daily_rate": value})
            self.assertEqual(response.status_code, 400, value)
            self.assertIn('id="daily_rate-error"', response.text)
        for value in ["0", "16", "2.5", "-2", "abc"]:
            self.assertEqual(self.post("/staff/vehicles/new", {**self.values, "seats": value}).status_code, 400, value)
        self.assertEqual(fleet.list_vehicles(self.database), [])

    def test_daily_rate_boundaries_are_stored_without_rounding(self):
        for index, (amount, cents) in enumerate([("0.01", 1), ("10.10", 1010), ("9999.99", 999999)]):
            vehicle = self.add_vehicle(fleet_code=f"RATE-{index}", daily_rate=amount)
            self.assertEqual(vehicle.daily_rate_cents, cents)
            self.assertEqual(vehicle.daily_rate, amount)

    def test_duplicate_fleet_ids_are_rejected_case_insensitively(self):
        vehicle = self.add_vehicle()
        response = self.post("/staff/vehicles/new", {**self.values, "fleet_code": "car-001"})
        self.assertEqual(response.status_code, 400)
        self.assertIn('id="fleet_code-error"', response.text)
        self.assertEqual(fleet.get_vehicle(self.database, vehicle.id), vehicle)

    def test_staff_can_edit_rate_branch_and_all_operational_statuses(self):
        vehicle = self.add_vehicle()
        for status in ["under_repair", "rented", "available"]:
            response = self.post(f"/staff/vehicles/{vehicle.id}/edit",
                                 {**self.values, "branch": "Toronto", "daily_rate": "55.25",
                                  "status": status, "version": str(vehicle.version)})
            self.assertEqual(response.status_code, 303)
            vehicle = fleet.get_vehicle(self.database, vehicle.id)
            self.assertEqual((vehicle.branch, vehicle.daily_rate_cents, vehicle.status), ("Toronto", 5525, status))
        self.assertEqual(len(fleet.list_vehicles(self.database)), 1)

    def test_duplicate_or_invalid_edits_leave_existing_records_unchanged(self):
        vehicle = self.add_vehicle()
        other = self.add_vehicle(fleet_code="CAR-002")
        path = f"/staff/vehicles/{other.id}/edit"
        self.assertEqual(self.post(path, {**self.values, "version": other.version}).status_code, 400)
        self.assertEqual(self.post(path, {**self.values, "version": other.version, "daily_rate": "-1"}).status_code, 400)
        self.assertEqual(fleet.get_vehicle(self.database, other.id), other)
        self.assertEqual(fleet.get_vehicle(self.database, vehicle.id), vehicle)

    def test_branch_and_status_filters_and_empty_state(self):
        self.assertIn("No vehicles to show", self.client.get("/staff/fleet").text)
        self.add_vehicle()
        self.add_vehicle(fleet_code="SUV-002", name="Honda CR-V", branch="Toronto", status="under_repair")
        response = self.client.get("/staff/fleet?branch=toronto&status=under_repair")
        self.assertIn("Honda CR-V", response.text)
        self.assertNotIn("Toyota Corolla", response.text)
        self.assertIn("No vehicles to show", self.client.get("/staff/fleet?status=rented").text)
        self.assertEqual(self.client.get("/staff/fleet?status=unknown").status_code, 400)

    def test_removal_requires_confirmation_and_retains_linked_records(self):
        vehicle = self.add_vehicle()
        # A test-only reference simulates future reservation links without implementing bookings.
        with get_connection(self.database) as connection:
            connection.executescript("CREATE TABLE historical_reference (vehicle_id INTEGER REFERENCES vehicles(id));")
            connection.execute("INSERT INTO historical_reference VALUES (?)", (vehicle.id,))
        path = f"/staff/vehicles/{vehicle.id}/remove"
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertIsNone(fleet.get_vehicle(self.database, vehicle.id).retired_at)
        self.assertEqual(self.post(path, {"version": vehicle.version}).status_code, 303)
        retired = fleet.get_vehicle(self.database, vehicle.id)
        self.assertIsNotNone(retired.retired_at)
        self.assertEqual(fleet.list_vehicles(self.database), [])
        self.assertEqual(fleet.list_vehicles(self.database, status="removed"), [retired])
        with get_connection(self.database) as connection:
            self.assertEqual(connection.execute("SELECT vehicle_id FROM historical_reference").fetchone()[0], vehicle.id)
        self.assertEqual(self.post(path, {"version": vehicle.version}).status_code, 303)
        self.assertEqual(fleet.get_vehicle(self.database, vehicle.id).retired_at, retired.retired_at)
        self.assertEqual(self.client.get(f"/staff/vehicles/{vehicle.id}/edit").status_code, 409)
        self.assertEqual(self.post(f"/staff/vehicles/{vehicle.id}/edit", {**self.values, "version": retired.version}).status_code, 409)
        self.assertEqual(self.post("/staff/vehicles/new").status_code, 400)

    def test_stale_edits_and_removals_cannot_overwrite_newer_data(self):
        original = self.add_vehicle()
        updated = fleet.save_vehicle(self.database, {**self.values, "daily_rate": "70", "version": original.version}, original.id)
        response = self.post(f"/staff/vehicles/{original.id}/edit", {**self.values, "version": original.version})
        self.assertEqual(response.status_code, 409)
        response = self.post(f"/staff/vehicles/{original.id}/remove", {"version": original.version})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(fleet.get_vehicle(self.database, original.id), updated)
        self.assertEqual(self.post(f"/staff/vehicles/{original.id}/edit", {**self.values, "version": "bad"}).status_code, 409)

    def test_all_mutations_require_csrf_and_same_origin(self):
        vehicle = self.add_vehicle()
        for path in ["/staff/vehicles/new", f"/staff/vehicles/{vehicle.id}/edit", f"/staff/vehicles/{vehicle.id}/remove"]:
            self.assertEqual(self.client.post(path, data=self.values).status_code, 403)
            data = {**self.values, "version": vehicle.version, "csrf_token": self.client.cookies[auth.CSRF_COOKIE]}
            self.assertEqual(self.client.post(path, data=data, headers={"Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(fleet.get_vehicle(self.database, vehicle.id), vehicle)

    def test_expired_session_cannot_change_fleet(self):
        with get_connection(self.database) as connection:
            connection.execute("UPDATE sessions SET expires_at = ?", (int(time.time()) - 1,))
        self.assertEqual(self.post("/staff/vehicles/new").status_code, 401)
        self.assertEqual(fleet.list_vehicles(self.database), [])

    def test_unknown_ids_return_404_and_out_of_range_ids_are_rejected(self):
        for action in ["edit", "remove"]:
            self.assertEqual(self.client.get(f"/staff/vehicles/999/{action}").status_code, 404)
            self.assertEqual(self.post(f"/staff/vehicles/999/{action}").status_code, 404)
            self.assertEqual(self.client.get(f"/staff/vehicles/{'9' * 30}/{action}").status_code, 422)

    def test_saved_text_is_escaped_and_filter_queries_are_bound_parameters(self):
        self.add_vehicle(name="<script>alert(1)</script>", branch="O'Brien")
        response = self.client.get("/staff/fleet", params={"branch": "O'Brien"})
        self.assertIn("&lt;script&gt;", response.text)
        self.assertNotIn("<script>alert(1)</script>", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        response = self.client.get("/staff/fleet", params={"branch": "' OR 1=1 --"})
        self.assertIn("No vehicles to show", response.text)

    def test_restore_preserves_details_status_and_references(self):
        vehicle = self.add_vehicle(status="under_repair")
        with get_connection(self.database) as connection:
            connection.execute("CREATE TABLE historical_reference (vehicle_id INTEGER REFERENCES vehicles(id))")
            connection.execute("INSERT INTO historical_reference VALUES (?)", (vehicle.id,))
        fleet.retire_vehicle(self.database, vehicle.id, str(vehicle.version))
        removed = fleet.get_vehicle(self.database, vehicle.id)
        listing = self.client.get("/staff/fleet?status=removed")
        self.assertIn("Restore vehicle", listing.text)
        self.assertIn("Previous status: Under repair", listing.text)
        response = self.post(f"/staff/vehicles/{vehicle.id}/restore", {"version": removed.version})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/staff/fleet?restored=1")
        restored = fleet.get_vehicle(self.database, vehicle.id)
        self.assertIsNone(restored.retired_at)
        self.assertEqual((restored.id, restored.name, restored.daily_rate_cents, restored.status),
                         (vehicle.id, vehicle.name, vehicle.daily_rate_cents, "under_repair"))
        self.assertEqual(fleet.list_vehicles(self.database, status="removed"), [])
        with get_connection(self.database) as connection:
            self.assertEqual(connection.execute("SELECT vehicle_id FROM historical_reference").fetchone()[0], vehicle.id)

    def test_restore_requires_staff_csrf_and_current_version(self):
        vehicle = self.add_vehicle()
        fleet.retire_vehicle(self.database, vehicle.id, str(vehicle.version))
        removed = fleet.get_vehicle(self.database, vehicle.id)
        path = f"/staff/vehicles/{vehicle.id}/restore"
        self.assertEqual(self.client.post(path, data={"version": removed.version}).status_code, 403)
        self.assertEqual(self.post(path, {"version": vehicle.version}).status_code, 409)
        with get_connection(self.database) as connection:
            connection.execute("UPDATE users SET role='customer' WHERE id=?", (self.staff.id,))
        self.assertEqual(self.post(path, {"version": removed.version}).status_code, 403)
        self.assertIsNotNone(fleet.get_vehicle(self.database, vehicle.id).retired_at)


if __name__ == "__main__":
    unittest.main()
