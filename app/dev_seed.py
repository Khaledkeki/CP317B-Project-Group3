"""LOCAL DEMO DATA ONLY. Run: python -m app.dev_seed

Adds fictitious demo accounts, vehicles, and bookings to your local database so every
Sprint 2 page has something to show. Safe to re-run: it does nothing if the demo data
already exists. Never run against a shared or real database.
"""

from datetime import datetime, timedelta
import secrets

from app.auth import register_user
from app.database import DATABASE_PATH, get_connection, initialize_database

PASSWORD = "demo-pass-123"
ACCOUNTS = [("Dana Customer", "dana@example.com", "demo_customer", "customer"),
            ("Sam Rivera", "sam@example.com", "demo_customer2", "customer"),
            ("Pat Staff", "pat@example.com", "demo_staff", "staff"),
            ("Morgan Manager", "morgan@example.com", "demo_manager", "manager")]
VEHICLES = [  # fleet_code, name, branch, type, category, seats, transmission, fuel, cents
    ("DEMO-01", "Toyota Corolla", "Waterloo", "sedan", "economy", 5, "automatic", "gasoline", 4900),
    ("DEMO-02", "Honda Civic", "Waterloo", "sedan", "standard", 5, "automatic", "gasoline", 5500),
    ("DEMO-03", "Toyota RAV4 Hybrid", "Waterloo", "suv", "standard", 5, "automatic", "hybrid", 7900),
    ("DEMO-04", "Tesla Model 3", "Waterloo", "sedan", "premium", 5, "automatic", "electric", 10900),
    ("DEMO-05", "Chrysler Pacifica", "Waterloo", "minivan", "standard", 7, "automatic", "gasoline", 9500),
    ("DEMO-06", "Mazda 3 Hatch", "Kitchener", "hatchback", "economy", 5, "manual", "gasoline", 4500),
    ("DEMO-07", "Ford F-150", "Kitchener", "truck", "standard", 5, "automatic", "gasoline", 11900),
    ("DEMO-08", "BMW 5 Series", "Kitchener", "sedan", "luxury", 5, "automatic", "gasoline", 18900),
]
# (username, fleet_code, pickup offset in hours from now, length in hours, status)
BOOKINGS = [
    ("demo_customer", "DEMO-01", -24 * 20, 72, "confirmed"),   # past
    ("demo_customer", "DEMO-03", -24 * 9, 48, "confirmed"),    # past
    ("demo_customer", "DEMO-02", -30, 24 * 3, "confirmed"),    # current (on rent)
    ("demo_customer", "DEMO-04", 24 * 6, 72, "confirmed"),     # upcoming (cancellable)
    ("demo_customer", "DEMO-05", 24 * 14, 48, "confirmed"),    # upcoming
    ("demo_customer", "DEMO-06", 24 * 3, 24, "cancelled"),     # cancelled
    ("demo_customer2", "DEMO-01", 3, 48, "confirmed"),         # pickup today
    ("demo_customer2", "DEMO-03", -48, None, "confirmed"),     # return due today
    ("demo_customer2", "DEMO-01", -24 * 25, 48, "confirmed"),
    ("demo_customer2", "DEMO-04", -24 * 15, 96, "confirmed"),
    ("demo_customer2", "DEMO-01", -24 * 5, 24, "confirmed"),
    ("demo_customer2", "DEMO-07", -24 * 12, 72, "confirmed"),
    ("demo_customer2", "DEMO-08", -24 * 4, 48, "confirmed"),
]


def fmt(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M")


def main():
    initialize_database()
    with get_connection() as connection:
        if connection.execute("SELECT 1 FROM vehicles WHERE fleet_code = 'DEMO-01'").fetchone():
            print("Demo data already exists. No changes made.")
            return
    users = {}
    for name, email, username, role in ACCOUNTS:
        with get_connection() as connection:
            row = connection.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        users[username] = row["id"] if row else register_user(DATABASE_PATH, name, email, username, PASSWORD, role=role).id
    now = datetime.now().replace(second=0, microsecond=0)
    end_of_today = now.replace(hour=20, minute=0)
    with get_connection() as connection:
        vehicles = {}
        for code, name, branch, kind, category, seats, transmission, fuel, cents in VEHICLES:
            cursor = connection.execute(
                "INSERT INTO vehicles (fleet_code, name, branch, vehicle_type, category, seats, transmission, "
                "fuel_type, daily_rate_cents, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'available')",
                (code, name, branch, kind, category, seats, transmission, fuel, cents))
            vehicles[code] = (cursor.lastrowid, name, branch, cents)
        for username, code, offset, hours, status in BOOKINGS:
            vehicle_id, name, branch, cents = vehicles[code]
            pickup = now + timedelta(hours=offset)
            return_time = pickup + timedelta(hours=hours) if hours else max(end_of_today, now + timedelta(hours=1))
            days = -(-(return_time - pickup) // timedelta(days=1))
            connection.execute(
                "INSERT INTO reservations (reference, request_key, user_id, vehicle_id, vehicle_name, branch, pickup, "
                "return_time, daily_rate_cents, charged_days, total_cents, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("CR-" + secrets.token_hex(8).upper(), secrets.token_urlsafe(32), users[username], vehicle_id, name,
                 branch, fmt(pickup), fmt(return_time), cents, days, cents * days, status))
    print("Demo data added. Sign in at http://127.0.0.1:8000/login with any of these (password: " + PASSWORD + "):")
    for _, _, username, role in ACCOUNTS:
        print(f"  {username:16} {role}")


if __name__ == "__main__":
    main()
