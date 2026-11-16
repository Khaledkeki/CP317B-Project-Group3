"""Sprint 2 PLACEHOLDER backend for the frontend pages (UI-03, UI-04, ADMIN-02, REP-01).

BACKEND OWNER: every function below is a minimal stand-in so the pages can be built
and demoed. Keep the function names, arguments, and return shapes (the templates
depend on them) and replace the bodies with the real implementation.

Times are stored as local "YYYY-MM-DDTHH:MM" strings, matching reservations.py.
"""

from datetime import date, datetime, timedelta
from pathlib import Path

from app.database import get_connection


class CancellationError(ValueError):
    """Raised with a customer-facing message when a booking cannot be cancelled."""


class ReturnError(ValueError):
    """Raised with a staff-facing message when a return cannot be recorded."""


# BACKEND: replace. Returns are kept in memory only (lost on restart) because the
# reservations table has no "returned" state yet. Suggested: add a returned_at column
# or a 'returned' status. Maps reservation reference -> "YYYY-MM-DDTHH:MM".
_RETURNED: dict[str, str] = {}


def now_minute() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def _phase(row, now: str) -> str:
    if row["status"] == "cancelled":
        return "cancelled"
    if row["reference"] in _RETURNED or row["return_time"] <= now:
        return "past"
    return "upcoming" if row["pickup"] > now else "current"


def rental_history(database: Path, user_id: int) -> dict[str, list[dict]]:
    """BACKEND: replace. UI-03 / FR4.

    Return the customer's own bookings grouped as
    {"current": [...], "upcoming": [...], "past": [...], "cancelled": [...]}.
    Each item is a dict with the reservations columns plus:
      "phase": one of the four group names,
      "can_cancel": bool (True only for confirmed bookings that have not started),
      "returned_at": "YYYY-MM-DDTHH:MM" or None.
    Current/upcoming sort soonest first; past/cancelled sort most recent first.
    """
    now = now_minute()
    with get_connection(database) as connection:
        rows = connection.execute("SELECT * FROM reservations WHERE user_id = ?", (user_id,)).fetchall()
    groups: dict[str, list[dict]] = {"current": [], "upcoming": [], "past": [], "cancelled": []}
    for row in rows:
        phase = _phase(row, now)
        groups[phase].append({**dict(row), "phase": phase, "can_cancel": phase == "upcoming",
                              "returned_at": _RETURNED.get(row["reference"])})
    groups["current"].sort(key=lambda item: item["return_time"])
    groups["upcoming"].sort(key=lambda item: item["pickup"])
    groups["past"].sort(key=lambda item: item["return_time"], reverse=True)
    groups["cancelled"].sort(key=lambda item: item["pickup"], reverse=True)
    return groups


def booking_phase(booking) -> dict:
    """BACKEND: replace. Phase and cancel eligibility for one booking row."""
    phase = _phase(booking, now_minute())
    return {"phase": phase, "can_cancel": phase == "upcoming", "returned_at": _RETURNED.get(booking["reference"])}


def cancel_reservation(database: Path, user_id: int, reference: str) -> None:
    """BACKEND: replace. UI-04 / FR5.

    Cancel the customer's own future booking. Raise CancellationError with a clear
    reason if it does not exist for this user, already started, or is already
    cancelled. Must be safe to repeat (a second request must not corrupt records).
    """
    with get_connection(database) as connection:
        row = connection.execute("SELECT * FROM reservations WHERE user_id = ? AND reference = ?",
                                 (user_id, reference)).fetchone()
        if row is None:
            raise CancellationError("We couldn't find that reservation on your account.")
        if row["status"] == "cancelled":
            raise CancellationError("This reservation is already cancelled.")
        if row["pickup"] <= now_minute():
            raise CancellationError("This rental has already started, so it can no longer be cancelled online.")
        connection.execute("UPDATE reservations SET status = 'cancelled' WHERE id = ? AND status = 'confirmed'",
                           (row["id"],))


def daily_activity(database: Path, branch: str, day: date) -> dict[str, list[dict]]:
    """BACKEND: replace. ADMIN-02 / FR7.

    For one branch and day, return confirmed bookings grouped as
    {"pickups": scheduled to start that day,
     "active": on rent throughout that day (started before, due back after),
     "returns": due back that day and not yet returned,
     "returned": return recorded for a booking due back that day}.
    Each item: reservation columns plus "customer_name" and "returned_at".
    """
    day_text = day.isoformat()
    with get_connection(database) as connection:
        rows = connection.execute(
            "SELECT reservations.*, users.name AS customer_name FROM reservations "
            "JOIN users ON users.id = reservations.user_id "
            "WHERE reservations.status = 'confirmed' AND reservations.branch = ? COLLATE NOCASE "
            "AND substr(reservations.pickup, 1, 10) <= ? AND substr(reservations.return_time, 1, 10) >= ? "
            "ORDER BY reservations.pickup", (branch, day_text, day_text)).fetchall()
    groups: dict[str, list[dict]] = {"pickups": [], "active": [], "returns": [], "returned": []}
    for row in rows:
        item = {**dict(row), "returned_at": _RETURNED.get(row["reference"])}
        pickup_day, return_day = row["pickup"][:10], row["return_time"][:10]
        if item["returned_at"]:
            if return_day == day_text or item["returned_at"][:10] == day_text:
                groups["returned"].append(item)
        elif pickup_day == day_text:
            groups["pickups"].append(item)
        elif return_day == day_text:
            groups["returns"].append(item)
        else:
            groups["active"].append(item)
    groups["returns"].sort(key=lambda item: item["return_time"])
    return groups


def record_return(database: Path, reference: str) -> None:
    """BACKEND: replace. ADMIN-02 / FR7.

    Mark a confirmed booking's vehicle as returned now. Raise ReturnError if the
    booking is unknown, cancelled, not yet picked up, or already returned.
    Should also keep vehicle status consistent (e.g. rented -> available).
    """
    with get_connection(database) as connection:
        row = connection.execute("SELECT * FROM reservations WHERE reference = ?", (reference,)).fetchone()
    if row is None or row["status"] != "confirmed":
        raise ReturnError("That booking can't be returned. Reload the page and try again.")
    if reference in _RETURNED:
        raise ReturnError("This vehicle's return was already recorded.")
    if row["pickup"] > now_minute():
        raise ReturnError("This rental hasn't started yet, so there is nothing to return.")
    _RETURNED[reference] = now_minute()


REPORT_GROUPS = {"model": "Vehicle model", "vehicle_type": "Vehicle type", "category": "Category"}


def utilization_report(database: Path, branch: str, start: date, end: date, group_by: str) -> list[dict]:
    """BACKEND: replace. REP-01 / FR8.

    Aggregate non-cancelled bookings with pickup between start and end (inclusive),
    optionally for one branch ("" = all branches), grouped by "model",
    "vehicle_type", or "category". Return rows sorted by rentals, most first:
    {"label": str, "rentals": int, "rental_days": int}. No customer details.
    """
    column = {"model": "reservations.vehicle_name", "vehicle_type": "vehicles.vehicle_type",
              "category": "vehicles.category"}[group_by]
    clauses = ["reservations.status = 'confirmed'", "substr(reservations.pickup, 1, 10) BETWEEN ? AND ?"]
    args: list = [start.isoformat(), end.isoformat()]
    if branch:
        clauses.append("reservations.branch = ? COLLATE NOCASE")
        args.append(branch)
    with get_connection(database) as connection:
        rows = connection.execute(
            f"SELECT {column} AS label, COUNT(*) AS rentals, SUM(reservations.charged_days) AS rental_days "
            "FROM reservations JOIN vehicles ON vehicles.id = reservations.vehicle_id "
            "WHERE " + " AND ".join(clauses) + f" GROUP BY {column} ORDER BY rentals DESC, label",
            args).fetchall()
    return [dict(row) for row in rows]


def default_report_period() -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=29), today
