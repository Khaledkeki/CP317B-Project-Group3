"""Backend checks for the Sprint 2 data layer: returns, cancellation,
daily grouping, utilization reporting, and schema migration."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import threading
import unittest

from fastapi.testclient import TestClient
from app import auth, fleet, reports, reservations
from app.database import get_connection, initialize_database
from app.main import create_app
from app.search import search_vehicles

LEGACY_SCHEMA = """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL,
        email TEXT NOT NULL COLLATE NOCASE UNIQUE,
        username TEXT NOT NULL COLLATE NOCASE UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'customer'
            CHECK (role IN ('customer', 'staff', 'manager'))
    );
    CREATE TABLE reservations (
        id INTEGER PRIMARY KEY,
        reference TEXT NOT NULL UNIQUE,
        request_key TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL REFERENCES users(id),
        vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
        vehicle_name TEXT NOT NULL,
        branch TEXT NOT NULL,
        pickup TEXT NOT NULL,
        return_time TEXT NOT NULL,
        daily_rate_cents INTEGER NOT NULL CHECK (daily_rate_cents > 0),
        charged_days INTEGER NOT NULL CHECK (charged_days > 0),
        total_cents INTEGER NOT NULL CHECK (total_cents > 0),
        status TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'cancelled')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (pickup < return_time)
    );
"""


def at(hours: float) -> str:
    return (datetime.now().replace(second=0, microsecond=0) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")


def day_at(hours: float) -> date:
    return date.fromisoformat(at(hours)[:10])


class Sprint2BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "rentals.db"
        self.key = "sprint2-backend-secret-" * 4
        self.client = TestClient(create_app(self.database, self.key), follow_redirects=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.customer = auth.register_user(self.database, "Dana Customer", "dana@example.test", "dana", "Password123!")
        self.other = auth.register_user(self.database, "Sam Other", "sam@example.test", "sam", "Password123!")
        self.staff = auth.register_user(self.database, "Pat Staff", "pat@example.test", "pat", "Password123!", role="staff")
        self.manager = auth.register_user(self.database, "Morgan Manager", "mo@example.test", "morgan", "Password123!", role="manager")
        base = {"name": "Toyota Corolla", "branch": "Waterloo", "vehicle_type": "sedan", "category": "economy",
                "seats": "5", "transmission": "automatic", "fuel_type": "gasoline", "daily_rate": "50.00",
                "status": "available"}
        self.car = fleet.save_vehicle(self.database, {**base, "fleet_code": "CAR-001"})
        self.suv = fleet.save_vehicle(self.database, {**base, "fleet_code": "CAR-002", "name": "Toyota RAV4",
                                                      "vehicle_type": "suv", "category": "standard"})

    def book(self, user, vehicle, pickup, return_time, status="confirmed", returned_at=None):
        reference = "CR-" + secrets.token_hex(8).upper()
        with get_connection(self.database) as connection:
            connection.execute(
                "INSERT INTO reservations (reference, request_key, user_id, vehicle_id, vehicle_name, branch, pickup, "
                "return_time, daily_rate_cents, charged_days, total_cents, status, returned_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 5000, 1, 5000, ?, ?)",
                (reference, secrets.token_urlsafe(32), user.id, vehicle.id, vehicle.name, vehicle.branch,
                 pickup, return_time, status, returned_at))
        return reference

    def groups_for(self, day: date, branch: str = "Waterloo") -> dict[str, list[str]]:
        return {key: [item["reference"] for item in items]
                for key, items in reports.daily_activity(self.database, branch, day).items()}

    def as_user(self, user):
        self.client.cookies.set(auth.SESSION_COOKIE, auth.create_session(self.database, self.key, user))

    def csrf(self, html):
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    # ---- returns persist ---------------------------------------------------------
    def test_recorded_return_survives_a_new_app_instance(self):
        reference = self.book(self.customer, self.car, at(-3), at(20))
        reservations.record_return(self.database, reference)
        with get_connection(self.database) as connection:
            stored = connection.execute("SELECT returned_at FROM reservations WHERE reference = ?",
                                        (reference,)).fetchone()["returned_at"]
        self.assertIsNotNone(stored)

        with TestClient(create_app(self.database, self.key), follow_redirects=False) as second:
            second.cookies.set(auth.SESSION_COOKIE, auth.create_session(self.database, self.key, self.customer))
            html = second.get("/account").text
        self.assertIn("Returned " + stored.replace("T", " "), html)
        self.assertIn(reference, html.split('id="past"')[1])

    def test_return_validation(self):
        future = self.book(self.customer, self.car, at(48), at(72))
        with self.assertRaises(reservations.ReturnError):
            reservations.record_return(self.database, future)
        cancelled = self.book(self.customer, self.car, at(-3), at(20), status="cancelled")
        with self.assertRaises(reservations.ReturnError):
            reservations.record_return(self.database, cancelled)
        with self.assertRaises(reservations.ReturnError):
            reservations.record_return(self.database, "CR-UNKNOWN")
        started = self.book(self.customer, self.car, at(-3), at(20))
        reservations.record_return(self.database, started)
        with self.assertRaises(reservations.ReturnError):
            reservations.record_return(self.database, started)
        with get_connection(self.database) as connection:
            self.assertIsNone(connection.execute(
                "SELECT returned_at FROM reservations WHERE reference = ?", (future,)).fetchone()["returned_at"])

    # ---- cancellation ------------------------------------------------------------
    def test_cancel_and_rebook_never_leave_two_confirmed_bookings(self):
        pickup, return_time = at(72), at(96)
        reference = self.book(self.customer, self.car, pickup, return_time)
        barrier = threading.Barrier(2)
        outcomes = {}

        def cancel():
            barrier.wait(timeout=10)
            try:
                reservations.cancel_reservation(self.database, self.customer.id, reference)
                outcomes["cancel"] = "ok"
            except reservations.CancellationError:
                outcomes["cancel"] = "refused"

        def rebook():
            barrier.wait(timeout=10)
            try:
                reservations.create_reservation(self.database, self.other.id, self.car.id, "Waterloo",
                                                pickup, return_time, str(self.car.version),
                                                secrets.token_urlsafe(32))
                outcomes["book"] = "ok"
            except reservations.ReservationError:
                outcomes["book"] = "refused"

        threads = [threading.Thread(target=cancel), threading.Thread(target=rebook)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
        self.assertEqual(outcomes["cancel"], "ok")
        with get_connection(self.database) as connection:
            overlapping = connection.execute(
                "SELECT COUNT(*) FROM reservations WHERE vehicle_id = ? AND status = 'confirmed' "
                "AND pickup < ? AND return_time > ?", (self.car.id, return_time, pickup)).fetchone()[0]
        self.assertLessEqual(overlapping, 1)

    def test_cancelled_dates_can_be_booked_again(self):
        reference = self.book(self.customer, self.car, at(72), at(96))
        reservations.cancel_reservation(self.database, self.customer.id, reference)
        new_key = secrets.token_urlsafe(32)
        again = reservations.create_reservation(self.database, self.other.id, self.car.id, "Waterloo",
                                                at(72), at(96), str(self.car.version), new_key)
        self.assertTrue(again.startswith("CR-"))

    def test_cancelling_a_returned_booking_is_refused(self):
        reference = self.book(self.customer, self.car, at(-3), at(72))
        reservations.record_return(self.database, reference)
        with self.assertRaises(reservations.CancellationError):
            reservations.cancel_reservation(self.database, self.customer.id, reference)
        with get_connection(self.database) as connection:
            self.assertEqual(connection.execute(
                "SELECT status FROM reservations WHERE reference = ?", (reference,)).fetchone()["status"],
                "confirmed")

    def test_concurrent_cancels_have_one_winner(self):
        reference = self.book(self.customer, self.car, at(72), at(96))
        barrier = threading.Barrier(2)

        def attempt(_):
            barrier.wait(timeout=10)
            try:
                reservations.cancel_reservation(self.database, self.customer.id, reference)
                return "cancelled"
            except reservations.CancellationError:
                return "refused"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, [0, 1]))
        self.assertEqual(outcomes.count("cancelled"), 1)
        self.assertEqual(outcomes.count("refused"), 1)

    def test_cancel_and_return_are_mutually_exclusive(self):
        future = self.book(self.customer, self.car, at(72), at(96))
        with self.assertRaises(reservations.ReturnError):
            reservations.record_return(self.database, future)
        started = self.book(self.customer, self.car, at(-1), at(96))
        with self.assertRaises(reservations.CancellationError):
            reservations.cancel_reservation(self.database, self.customer.id, started)
        reservations.cancel_reservation(self.database, self.customer.id, future)
        reservations.record_return(self.database, started)
        with get_connection(self.database) as connection:
            row = connection.execute("SELECT status, returned_at FROM reservations WHERE reference = ?",
                                     (started,)).fetchone()
        self.assertEqual(row["status"], "confirmed")
        self.assertIsNotNone(row["returned_at"])

    # ---- daily grouping ----------------------------------------------------------
    def test_same_day_rental_is_a_pickup_then_a_return(self):
        today = date.today()
        reference = self.book(self.customer, self.car, f"{today.isoformat()}T00:00", f"{today.isoformat()}T23:59")
        self.assertIn(reference, self.groups_for(today)["pickups"])
        reservations.record_return(self.database, reference)
        groups = self.groups_for(today)
        self.assertIn(reference, groups["returned"])
        self.assertNotIn(reference, groups["pickups"])

    def test_early_return_does_not_reappear_on_the_scheduled_day(self):
        reference = self.book(self.customer, self.car, at(-48), at(48))
        reservations.record_return(self.database, reference)
        self.assertIn(reference, self.groups_for(date.today())["returned"])
        scheduled = day_at(48)
        listed = {key for key, refs in self.groups_for(scheduled).items() if reference in refs}
        self.assertEqual(listed, set())
        self.assertNotIn(reference, self.groups_for(day_at(24))["active"])

    def test_returned_booking_is_never_on_rent(self):
        reference = self.book(self.other, self.suv, at(-48), at(48))
        reservations.record_return(self.database, reference)
        listed = {key for key, refs in self.groups_for(date.today()).items() if reference in refs}
        self.assertEqual(listed, {"returned"})

    # ---- utilization -------------------------------------------------------------
    def test_report_excludes_cancelled_and_includes_returned(self):
        for _ in range(2):
            self.book(self.customer, self.suv, at(-100), at(-80))
        returned = self.book(self.other, self.car, at(-60), at(-40))
        reservations.record_return(self.database, returned)
        self.book(self.other, self.car, at(-30), at(-20), status="cancelled")
        today = date.today()
        rows = reports.utilization_report(self.database, "", today - timedelta(days=10), today, "model")
        self.assertEqual([(row["label"], row["rentals"]) for row in rows],
                         [("Toyota RAV4", 2), ("Toyota Corolla", 1)])

    def test_grouping_options_agree_and_period_is_inclusive(self):
        start, end = date.today() - timedelta(days=5), date.today()
        self.book(self.customer, self.car, f"{start.isoformat()}T12:00", f"{start.isoformat()}T23:00")
        self.book(self.other, self.suv, f"{end.isoformat()}T00:30", f"{end.isoformat()}T23:30")
        by_model = reports.utilization_report(self.database, "", start, end, "model")
        by_type = reports.utilization_report(self.database, "", start, end, "vehicle_type")
        by_category = reports.utilization_report(self.database, "", start, end, "category")
        self.assertEqual(sum(row["rentals"] for row in by_model), 2)
        self.assertEqual(sum(row["rentals"] for row in by_type), 2)
        self.assertEqual(sum(row["rentals"] for row in by_category), 2)

    # ---- pages -------------------------------------------------------------------
    def test_returned_booking_shows_as_completed_without_cancel_link(self):
        reference = self.book(self.customer, self.car, at(-3), at(72))
        reservations.record_return(self.database, reference)
        self.as_user(self.customer)
        html = self.client.get("/account").text
        self.assertIn(reference, html.split('id="past"')[1])
        self.assertNotIn(f"/reservations/{reference}/cancel", html)

    def test_cancel_page_refuses_a_returned_booking(self):
        reference = self.book(self.customer, self.car, at(-3), at(72))
        reservations.record_return(self.database, reference)
        self.as_user(self.customer)
        page = self.client.get(f"/reservations/{reference}/cancel")
        self.assertIn("can&rsquo;t be cancelled", page.text)
        response = self.client.post(f"/reservations/{reference}/cancel", data={"csrf_token": self.csrf(page.text)})
        self.assertEqual(response.status_code, 409)

    def test_late_return_is_listed_on_the_day_it_was_recorded(self):
        reference = self.book(self.customer, self.car, at(-72), at(-24))
        before = {key for key, refs in self.groups_for(date.today()).items() if reference in refs}
        self.assertNotIn("returned", before)
        reservations.record_return(self.database, reference)
        after = {key for key, refs in self.groups_for(date.today()).items() if reference in refs}
        self.assertEqual(after, {"returned"})

    def test_daily_activity_is_scoped_to_the_requested_branch(self):
        kitchener = fleet.save_vehicle(self.database, {
            "fleet_code": "CAR-003", "name": "Mazda 3", "branch": "Kitchener", "vehicle_type": "hatchback",
            "category": "economy", "seats": "5", "transmission": "manual", "fuel_type": "gasoline",
            "daily_rate": "40.00", "status": "available"})
        other = self.book(self.other, kitchener, at(-30), at(40))
        self.assertIn(other, self.groups_for(date.today(), "Kitchener")["active"])
        self.assertEqual([], [ref for refs in self.groups_for(date.today(), "Waterloo").values()
                              for ref in refs if ref == other])

    def test_return_endpoint_is_staff_only(self):
        reference = self.book(self.customer, self.car, at(-3), at(20))
        self.as_user(self.customer)
        page = self.client.get("/account").text
        response = self.client.post("/staff/daily/return", data={"csrf_token": self.csrf(page),
                                                                  "reference": reference})
        self.assertEqual(response.status_code, 403)
        with get_connection(self.database) as connection:
            self.assertIsNone(connection.execute(
                "SELECT returned_at FROM reservations WHERE reference = ?", (reference,)).fetchone()["returned_at"])
        self.as_user(self.staff)
        wrong_csrf = self.client.post("/staff/daily/return", data={"csrf_token": "wrong", "reference": reference})
        self.assertEqual(wrong_csrf.status_code, 403)
        with get_connection(self.database) as connection:
            self.assertIsNone(connection.execute(
                "SELECT returned_at FROM reservations WHERE reference = ?", (reference,)).fetchone()["returned_at"])

    def test_report_rejects_an_unknown_grouping(self):
        self.as_user(self.manager)
        response = self.client.get("/manager/reports/utilization", params={"group_by": "drop table"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Choose how to group the report.", response.text)
        self.as_user(self.customer)
        self.assertEqual(self.client.get("/manager/reports/utilization").status_code, 403)

    # ---- lifecycle boundaries ----------------------------------------------------
    def test_phase_boundaries_are_inclusive_at_the_edges(self):
        now = "2026-11-15T12:00"

        def row(**changes):
            base = {"status": "confirmed", "returned_at": None,
                    "pickup": "2026-11-15T12:00", "return_time": "2026-11-16T12:00"}
            return {**base, **changes}

        self.assertEqual(reservations.phase(row(), now), "current")
        self.assertEqual(reservations.phase(row(pickup="2026-11-15T12:01"), now), "upcoming")
        self.assertEqual(reservations.phase(row(return_time="2026-11-15T12:00"), now), "past")
        self.assertEqual(reservations.phase(row(return_time="2026-11-15T12:01"), now), "current")
        self.assertEqual(reservations.phase(row(status="cancelled"), now), "cancelled")
        self.assertEqual(reservations.phase(row(returned_at="2026-11-15T11:00"), now), "past")
        self.assertEqual(reservations.phase(
            row(pickup="2026-12-31T23:59", return_time="2027-01-01T00:00"), "2027-01-01T00:00"), "past")

    # ---- reporting boundaries ----------------------------------------------------
    def test_report_includes_retired_vehicles_and_ignores_branch_case(self):
        self.book(self.customer, self.car, at(-100), at(-80))
        fleet.retire_vehicle(self.database, self.car.id, str(self.car.version))
        today = date.today()
        rows = reports.utilization_report(self.database, "waterloo", today - timedelta(days=10), today, "model")
        self.assertEqual([(row["label"], row["rentals"]) for row in rows], [("Toyota Corolla", 1)])

    def test_report_period_limit_is_enforced_at_366_days(self):
        self.as_user(self.manager)
        at_limit = self.client.get("/manager/reports/utilization",
                                   params={"start": "2025-01-01", "end": "2026-01-02"})
        self.assertEqual(at_limit.status_code, 200)
        over_limit = self.client.get("/manager/reports/utilization",
                                     params={"start": "2025-01-01", "end": "2026-01-03"})
        self.assertEqual(over_limit.status_code, 400)
        self.assertIn("one year or less", over_limit.text)

    # ---- untrusted content and request origin --------------------------------------
    def test_vehicle_text_is_escaped_on_every_sprint2_page(self):
        evil = fleet.save_vehicle(self.database, {
            "fleet_code": "CAR-XSS", "name": "<script>alert('x')</script>", "branch": "Waterloo",
            "vehicle_type": "sedan", "category": "economy", "seats": "5",
            "transmission": "automatic", "fuel_type": "gasoline", "daily_rate": "10.00",
            "status": "available"})
        reference = self.book(self.customer, evil, at(-2), at(20))
        self.as_user(self.customer)
        account = self.client.get("/account").text
        self.assertIn(reference, account)
        self.assertNotIn("<script>alert", account)
        self.assertIn("&lt;script&gt;alert", account)
        self.as_user(self.staff)
        daily = self.client.get("/staff/daily", params={"branch": "Waterloo"}).text
        self.assertNotIn("<script>alert", daily)
        self.as_user(self.manager)
        report = self.client.get("/manager/reports/utilization").text
        self.assertNotIn("<script>alert", report)

    def test_form_posts_from_another_origin_are_rejected(self):
        reference = self.book(self.customer, self.car, at(72), at(96))
        self.as_user(self.customer)
        token = self.csrf(self.client.get("/account").text)
        blocked = self.client.post(f"/reservations/{reference}/cancel", data={"csrf_token": token},
                                   headers={"origin": "https://evil.example"})
        self.assertEqual(blocked.status_code, 403)
        with get_connection(self.database) as connection:
            self.assertEqual(connection.execute("SELECT status FROM reservations WHERE reference = ?",
                                                (reference,)).fetchone()["status"], "confirmed")

    def test_returning_a_car_early_keeps_the_original_dates_held(self):
        reference = self.book(self.customer, self.car, at(-2), at(96))
        reservations.record_return(self.database, reference)
        vehicles, errors = search_vehicles(self.database, {"branch": "Waterloo", "pickup": at(24),
                                                          "return_time": at(48)})
        self.assertEqual(errors, {})
        self.assertNotIn(self.car.id, [vehicle.id for vehicle in vehicles])

    # ---- migration ---------------------------------------------------------------
    def test_legacy_database_gains_the_column_without_data_loss(self):
        legacy = Path(self.temp.name) / "legacy.db"
        connection = sqlite3.connect(legacy)
        connection.executescript(LEGACY_SCHEMA)
        connection.execute(
            "INSERT INTO reservations (reference, request_key, user_id, vehicle_id, vehicle_name, branch, pickup, "
            "return_time, daily_rate_cents, charged_days, total_cents) "
            "VALUES ('CR-LEGACY', 'legacy-key', 1, 1, 'Old Car', 'Waterloo', '2026-01-01T09:00', "
            "'2026-01-02T09:00', 5000, 1, 5000)")
        connection.commit()
        connection.close()

        initialize_database(legacy)
        initialize_database(legacy)  # idempotent

        with get_connection(legacy) as connection:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(reservations)")}
            row = connection.execute("SELECT * FROM reservations WHERE reference = 'CR-LEGACY'").fetchone()
            indexes = {row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'reservations'")}
        self.assertIn("returned_at", columns)
        self.assertIsNone(row["returned_at"])
        self.assertEqual(row["vehicle_name"], "Old Car")
        self.assertLessEqual({"reservations_customer", "reservations_daily"}, indexes)


if __name__ == "__main__":
    unittest.main()
