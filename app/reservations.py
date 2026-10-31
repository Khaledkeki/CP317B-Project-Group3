"""Atomic reservation creation and customer-owned reservation queries."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import re
import secrets

from app.database import get_connection
from app.search import validate_dates


class ReservationError(ValueError):
    pass


@dataclass(frozen=True)
class Quote:
    vehicle_id: int
    version: int
    name: str
    branch: str
    pickup: str
    return_time: str
    rate_cents: int
    days: int
    total_cents: int

    @property
    def rate(self):
        return money(self.rate_cents)

    @property
    def total(self):
        return money(self.total_cents)


def money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def quote_on_connection(connection, vehicle_id: int, branch: str, pickup: str, return_time: str) -> Quote:
    dates, errors = validate_dates({"pickup": pickup, "return_time": return_time})
    if errors:
        raise ReservationError(" ".join(errors.values()))
    vehicle = connection.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    if (vehicle is None or vehicle["retired_at"] is not None or vehicle["status"] != "available"
            or not branch or vehicle["branch"].casefold() != branch.strip().casefold()):
        raise ReservationError("This vehicle is no longer available at the selected branch. Please search again.")
    overlap = connection.execute(
        "SELECT 1 FROM reservations WHERE vehicle_id = ? AND status = 'confirmed' "
        "AND pickup < ? AND return_time > ? LIMIT 1", (vehicle_id, return_time, pickup),
    ).fetchone()
    if overlap:
        raise ReservationError("This vehicle is no longer available for those dates. Please search again.")
    duration = dates["return_time"] - dates["pickup"]
    days = (duration // timedelta(days=1)) + (1 if duration % timedelta(days=1) else 0)
    return Quote(vehicle_id, vehicle["version"], vehicle["name"], vehicle["branch"], pickup, return_time,
                 vehicle["daily_rate_cents"], days, vehicle["daily_rate_cents"] * days)


def get_quote(database: Path, vehicle_id: int, branch: str, pickup: str, return_time: str) -> Quote:
    with get_connection(database) as connection:
        return quote_on_connection(connection, vehicle_id, branch, pickup, return_time)


def create_reservation(database: Path, user_id: int, vehicle_id: int, branch: str, pickup: str,
                       return_time: str, version: str, request_key: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,80}", request_key):
        raise ReservationError("This booking form has expired. Please review the vehicle again.")
    with get_connection(database) as connection:
        # Reserve the SQLite write lock before checking availability, rate, and user role.
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user or user["role"] != "customer":
            raise ReservationError("Please sign in with a customer account to reserve a car.")
        prior = connection.execute("SELECT * FROM reservations WHERE request_key = ?", (request_key,)).fetchone()
        if prior:
            if (prior["user_id"] == user_id and prior["vehicle_id"] == vehicle_id and prior["pickup"] == pickup
                    and prior["return_time"] == return_time and prior["branch"].casefold() == branch.strip().casefold()):
                return prior["reference"]
            raise ReservationError("This booking form was already used. Please search and review again.")
        quote = quote_on_connection(connection, vehicle_id, branch, pickup, return_time)
        if not re.fullmatch(r"[0-9]{1,18}", version) or int(version) != quote.version:
            raise ReservationError("The vehicle details or rate changed. Please search and review the updated details.")
        reference = "CR-" + secrets.token_hex(8).upper()
        connection.execute(
            "INSERT INTO reservations (reference, request_key, user_id, vehicle_id, vehicle_name, branch, "
            "pickup, return_time, daily_rate_cents, charged_days, total_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (reference, request_key, user_id, quote.vehicle_id, quote.name, quote.branch, quote.pickup,
             quote.return_time, quote.rate_cents, quote.days, quote.total_cents),
        )
        return reference


def get_reservation(database: Path, user_id: int, reference: str):
    with get_connection(database) as connection:
        return connection.execute("SELECT * FROM reservations WHERE user_id = ? AND reference = ?", (user_id, reference)).fetchone()


def list_reservations(database: Path, user_id: int):
    with get_connection(database) as connection:
        return connection.execute("SELECT * FROM reservations WHERE user_id = ? ORDER BY pickup DESC, id DESC", (user_id,)).fetchall()
