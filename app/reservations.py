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


class CancellationError(ValueError):
    """Raised with a customer-facing message when a booking cannot be cancelled."""


class ReturnError(ValueError):
    """Raised with a staff-facing message when a return cannot be recorded."""


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


def now_minute() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def phase(row, now: str) -> str:
    """Lifecycle group for one reservation row."""
    if row["status"] == "cancelled":
        return "cancelled"
    if row["returned_at"] or row["return_time"] <= now:
        return "past"
    return "current" if row["pickup"] <= now else "upcoming"


def booking_phase(booking) -> dict:
    current = phase(booking, now_minute())
    return {"phase": current, "can_cancel": current == "upcoming", "returned_at": booking["returned_at"]}


def rental_history(database: Path, user_id: int) -> dict[str, list[dict]]:
    """The customer's own bookings grouped into current, upcoming, past and cancelled."""
    now = now_minute()
    with get_connection(database) as connection:
        rows = connection.execute("SELECT * FROM reservations WHERE user_id = ?", (user_id,)).fetchall()
    groups: dict[str, list[dict]] = {"current": [], "upcoming": [], "past": [], "cancelled": []}
    for row in rows:
        current = phase(row, now)
        groups[current].append({**dict(row), "phase": current, "can_cancel": current == "upcoming"})
    groups["current"].sort(key=lambda item: item["return_time"])
    groups["upcoming"].sort(key=lambda item: item["pickup"])
    groups["past"].sort(key=lambda item: item["return_time"], reverse=True)
    groups["cancelled"].sort(key=lambda item: item["pickup"], reverse=True)
    return groups


def cancel_reservation(database: Path, user_id: int, reference: str) -> None:
    """Cancel one of the customer's own future bookings."""
    now = now_minute()
    with get_connection(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM reservations WHERE user_id = ? AND reference = ?",
                                 (user_id, reference)).fetchone()
        if row is None:
            raise CancellationError("We couldn't find that reservation on your account.")
        if row["status"] == "cancelled":
            raise CancellationError("This reservation is already cancelled.")
        if row["returned_at"]:
            raise CancellationError("This rental has already been returned.")
        if row["pickup"] <= now:
            raise CancellationError("This rental has already started, so it can no longer be cancelled online.")
        updated = connection.execute(
            "UPDATE reservations SET status = 'cancelled' "
            "WHERE id = ? AND status = 'confirmed' AND returned_at IS NULL", (row["id"],))
        if updated.rowcount != 1:
            raise CancellationError("This reservation is already cancelled.")


def record_return(database: Path, reference: str) -> None:
    """Mark a confirmed booking's vehicle as returned now."""
    now = now_minute()
    with get_connection(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM reservations WHERE reference = ?", (reference,)).fetchone()
        if row is None or row["status"] != "confirmed":
            raise ReturnError("That booking can't be returned. Reload the page and try again.")
        if row["returned_at"]:
            raise ReturnError("This vehicle's return was already recorded.")
        if row["pickup"] > now:
            raise ReturnError("This rental hasn't started yet, so there is nothing to return.")
        updated = connection.execute("UPDATE reservations SET returned_at = ? WHERE id = ? AND returned_at IS NULL",
                                     (now, row["id"]))
        if updated.rowcount != 1:
            raise ReturnError("This vehicle's return was already recorded.")
