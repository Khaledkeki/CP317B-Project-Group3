"""Sprint 2 page checks: rental history (UI-03), cancellation (UI-04),
daily rental monitoring (ADMIN-02), and the utilization report (REP-01).

These test the pages against the placeholder data layer (app.sprint2_placeholders).
"""

from datetime import date, datetime, timedelta
from pathlib import Path
import re
import secrets
import tempfile
import unittest

from fastapi.testclient import TestClient
from app import auth, fleet, sprint2_placeholders
from app.database import get_connection
from app.main import create_app


def at(hours: float) -> str:
    return (datetime.now().replace(second=0, microsecond=0) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")


class Sprint2PageTests(unittest.TestCase):
    def setUp(self):
        sprint2_placeholders._RETURNED.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "rentals.db"
        self.key = "sprint2-tests-secret-" * 4
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

    def book(self, user, vehicle, pickup, return_time, status="confirmed"):
        reference = "CR-" + secrets.token_hex(8).upper()
        with get_connection(self.database) as connection:
            connection.execute(
                "INSERT INTO reservations (reference, request_key, user_id, vehicle_id, vehicle_name, branch, pickup, "
                "return_time, daily_rate_cents, charged_days, total_cents, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 5000, 1, 5000, ?)",
                (reference, secrets.token_urlsafe(32), user.id, vehicle.id, vehicle.name, vehicle.branch,
                 pickup, return_time, status))
        return reference

    def as_user(self, user):
        self.client.cookies.set(auth.SESSION_COOKIE, auth.create_session(self.database, self.key, user))

    def csrf(self, html):
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    # ---- UI-03 rental history ----------------------------------------------------
    def test_history_groups_current_upcoming_past_and_cancelled(self):
        current = self.book(self.customer, self.car, at(-5), at(20))
        upcoming = self.book(self.customer, self.suv, at(72), at(96))
        past = self.book(self.customer, self.suv, at(-200), at(-150))
        cancelled = self.book(self.customer, self.car, at(100), at(120), status="cancelled")
        self.as_user(self.customer)
        html = self.client.get("/account").text
        sections = {key: html.split(f'id="{key}"')[1].split("</section>")[0]
                    for key in ("current", "upcoming", "past", "cancelled")}
        self.assertIn(current, sections["current"])
        self.assertIn(upcoming, sections["upcoming"])
        self.assertIn(past, sections["past"])
        self.assertIn(cancelled, sections["cancelled"])
        self.assertIn("On rent now", sections["current"])
        self.assertIn("Cancelled", sections["cancelled"])  # status shown as text, not only colour

    def test_history_shows_only_own_bookings_and_empty_state(self):
        self.book(self.other, self.car, at(72), at(96))
        self.as_user(self.customer)
        html = self.client.get("/account").text
        self.assertIn("No rentals yet", html)
        self.assertNotIn("Sam Other", html)

    def test_only_upcoming_bookings_offer_cancel(self):
        current = self.book(self.customer, self.car, at(-5), at(20))
        upcoming = self.book(self.customer, self.suv, at(72), at(96))
        self.as_user(self.customer)
        html = self.client.get("/account").text
        self.assertIn(f"/reservations/{upcoming}/cancel", html)
        self.assertNotIn(f"/reservations/{current}/cancel", html)

    # ---- UI-04 cancellation ------------------------------------------------------
    def test_cancel_requires_confirmation_then_releases_dates(self):
        reference = self.book(self.customer, self.car, at(72), at(96))
        self.as_user(self.customer)
        page = self.client.get(f"/reservations/{reference}/cancel")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Cancel this reservation?", page.text)
        self.assertIn("Keep reservation", page.text)
        response = self.client.post(f"/reservations/{reference}/cancel", data={"csrf_token": self.csrf(page.text)})
        self.assertEqual(response.status_code, 303)
        self.assertIn(f"cancelled={reference}", response.headers["location"])
        account = self.client.get(response.headers["location"]).text
        self.assertIn("was cancelled", account)
        self.assertIn(reference, account.split('id="cancelled"')[1])
        search = self.client.get("/search", params={"branch": "Waterloo", "pickup": at(72)[:16], "return_time": at(96)})
        self.assertIn("Toyota Corolla", search.text)

    def test_repeat_cancel_and_started_rental_are_explained(self):
        reference = self.book(self.customer, self.car, at(72), at(96))
        started = self.book(self.customer, self.suv, at(-5), at(20))
        self.as_user(self.customer)
        token = self.csrf(self.client.get("/account").text)
        self.client.post(f"/reservations/{reference}/cancel", data={"csrf_token": token})
        again = self.client.post(f"/reservations/{reference}/cancel", data={"csrf_token": token})
        self.assertEqual(again.status_code, 409)
        self.assertIn("already cancelled", again.text)
        page = self.client.get(f"/reservations/{started}/cancel")
        self.assertIn("can&rsquo;t be cancelled", page.text)
        self.assertNotIn('action="/reservations/' + started + '/cancel"', page.text)
        blocked = self.client.post(f"/reservations/{started}/cancel", data={"csrf_token": token})
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("already started", blocked.text)

    def test_cannot_cancel_another_customers_booking_or_without_csrf(self):
        reference = self.book(self.other, self.car, at(72), at(96))
        self.as_user(self.customer)
        self.assertEqual(self.client.get(f"/reservations/{reference}/cancel").status_code, 404)
        token = self.csrf(self.client.get("/account").text)
        self.assertEqual(self.client.post(f"/reservations/{reference}/cancel", data={"csrf_token": token}).status_code, 404)
        own = self.book(self.customer, self.car, at(150), at(170))
        self.assertEqual(self.client.post(f"/reservations/{own}/cancel", data={"csrf_token": "wrong"}).status_code, 403)

    # ---- ADMIN-02 daily rental monitoring -----------------------------------------
    def test_daily_page_groups_pickups_active_and_returns(self):
        today = date.today().isoformat()
        pickup = self.book(self.customer, self.car, f"{today}T23:58", at(48))
        returning = self.book(self.other, self.suv, at(-48), f"{today}T23:59")
        self.as_user(self.staff)
        html = self.client.get("/staff/daily", params={"branch": "Waterloo", "day": today}).text
        self.assertIn(pickup, html.split('id="pickups"')[1].split("</section>")[0])
        self.assertIn(returning, html.split('id="returns"')[1].split("</section>")[0])
        self.assertIn("Dana Customer", html)

    def test_staff_can_record_a_return(self):
        today = date.today().isoformat()
        reference = self.book(self.other, self.suv, at(-48), f"{today}T23:59")
        self.as_user(self.staff)
        page = self.client.get("/staff/daily", params={"branch": "Waterloo", "day": today})
        response = self.client.post("/staff/daily/return", data={"csrf_token": self.csrf(page.text), "reference": reference,
                                                                  "branch": "Waterloo", "day": today})
        self.assertEqual(response.status_code, 303)
        html = self.client.get(response.headers["location"]).text
        self.assertIn(f"Return recorded for {reference}", html)
        self.assertIn(reference, html.split('id="returned"')[1].split("</section>")[0])

    def test_daily_page_is_staff_only_and_validates_input(self):
        self.as_user(self.customer)
        self.assertEqual(self.client.get("/staff/daily").status_code, 403)
        self.as_user(self.staff)
        self.assertEqual(self.client.get("/staff/daily", params={"day": "not-a-date"}).status_code, 400)
        self.assertEqual(self.client.get("/staff/daily", params={"branch": "Nowhere"}).status_code, 400)

    # ---- REP-01 utilization report ------------------------------------------------
    def test_report_ranks_vehicles_and_excludes_cancelled(self):
        for _ in range(2):
            self.book(self.customer, self.suv, at(-100), at(-80))
        self.book(self.other, self.car, at(-60), at(-40))
        self.book(self.other, self.car, at(-30), at(-20), status="cancelled")
        self.as_user(self.manager)
        html = self.client.get("/manager/reports/utilization").text
        rows = re.findall(r'<th scope="row"[^>]*>([^<]+)</th>\s*<td class="num" data-label="Rentals">(\d+)', html)
        self.assertEqual(rows, [("Toyota RAV4", "2"), ("Toyota Corolla", "1")])
        self.assertNotIn("Dana Customer", html)
        by_category = self.client.get("/manager/reports/utilization", params={"group_by": "category"}).text
        self.assertIn(">Standard</th>", by_category)

    def test_report_is_manager_only_and_handles_empty_and_invalid_periods(self):
        self.as_user(self.staff)
        self.assertEqual(self.client.get("/manager/reports/utilization").status_code, 403)
        self.as_user(self.manager)
        self.assertIn("No rentals in this period", self.client.get("/manager/reports/utilization").text)
        bad = self.client.get("/manager/reports/utilization", params={"start": "2026-12-01", "end": "2026-01-01"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("End date must be on or after the start date.", bad.text)


if __name__ == "__main__":
    unittest.main()
