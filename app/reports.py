"""Branch operations and utilization queries for the Sprint 2 staff pages."""

from datetime import date, timedelta
from pathlib import Path

from app.database import get_connection

REPORT_GROUPS = {"model": "Vehicle model", "vehicle_type": "Vehicle type", "category": "Category"}


def default_report_period() -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=29), today


def daily_activity(database: Path, branch: str, day: date) -> dict[str, list[dict]]:
    """One branch's confirmed bookings grouped for one day.

    Each booking appears in a single group. A return recorded on the day wins over
    the pickup, pickup over the scheduled return, and everything else is on rent.
    """
    day_text = day.isoformat()
    with get_connection(database) as connection:
        rows = connection.execute(
            "SELECT reservations.*, users.name AS customer_name FROM reservations "
            "JOIN users ON users.id = reservations.user_id "
            "WHERE reservations.status = 'confirmed' AND reservations.branch = ? COLLATE NOCASE "
            "AND (substr(reservations.pickup, 1, 10) <= ? AND substr(reservations.return_time, 1, 10) >= ? "
            "OR substr(reservations.returned_at, 1, 10) = ?) "
            "ORDER BY reservations.pickup", (branch, day_text, day_text, day_text)).fetchall()
    groups: dict[str, list[dict]] = {"pickups": [], "active": [], "returns": [], "returned": []}
    for row in rows:
        item = dict(row)
        if item["returned_at"]:
            if item["returned_at"][:10] == day_text:
                groups["returned"].append(item)
        elif item["pickup"][:10] == day_text:
            groups["pickups"].append(item)
        elif item["return_time"][:10] == day_text:
            groups["returns"].append(item)
        else:
            groups["active"].append(item)
    groups["pickups"].sort(key=lambda item: item["pickup"])
    groups["returns"].sort(key=lambda item: item["return_time"])
    groups["returned"].sort(key=lambda item: item["returned_at"])
    return groups


def utilization_report(database: Path, branch: str, start: date, end: date, group_by: str) -> list[dict]:
    """Confirmed bookings picked up inside the period, grouped and ranked by rentals."""
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
