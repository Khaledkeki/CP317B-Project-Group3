"""SQLite connection helpers; no third-party database server is required."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from collections.abc import Iterator


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_DIR / "data" / "car_rental.db"


@contextmanager
def get_connection(database_path: Path = DATABASE_PATH) -> Iterator[sqlite3.Connection]:
    """Open one connection, commit successful work, and always close it."""
    connection = sqlite3.connect(database_path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database(database_path: Path = DATABASE_PATH) -> None:
    """Create prototype tables while retaining existing records."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(database_path) as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'customer'
                    CHECK (role IN ('customer', 'staff', 'manager'))
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY,
                fleet_code TEXT NOT NULL COLLATE NOCASE UNIQUE,
                name TEXT NOT NULL,
                branch TEXT NOT NULL COLLATE NOCASE,
                vehicle_type TEXT NOT NULL,
                category TEXT NOT NULL,
                seats INTEGER NOT NULL CHECK (seats BETWEEN 1 AND 15),
                transmission TEXT NOT NULL CHECK (transmission IN ('automatic', 'manual')),
                fuel_type TEXT NOT NULL,
                daily_rate_cents INTEGER NOT NULL CHECK (daily_rate_cents BETWEEN 1 AND 999999),
                status TEXT NOT NULL CHECK (status IN ('available', 'rented', 'under_repair')),
                retired_at TEXT,
                version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS reservations (
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
            CREATE INDEX IF NOT EXISTS reservations_availability
                ON reservations(vehicle_id, status, pickup, return_time);
        """)
