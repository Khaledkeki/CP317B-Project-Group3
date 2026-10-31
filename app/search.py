"""Customer vehicle search with reservation-aware date availability."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path
import re

from app.database import get_connection
from app.fleet import CHOICES
from app.models import Vehicle

FILTERS = ("branch", "pickup", "return_time", "vehicle_type", "category", "max_price", "seats", "transmission", "fuel_type")


def search_branches(database: Path) -> list[str]:
    with get_connection(database) as connection:
        return [row[0] for row in connection.execute(
            "SELECT DISTINCT branch FROM vehicles WHERE retired_at IS NULL ORDER BY branch COLLATE NOCASE")]


def validate_dates(values: dict[str, str]) -> tuple[dict[str, datetime], dict[str, str]]:
    errors = {}
    dates = {}
    for field, label in (("pickup", "pickup"), ("return_time", "return")):
        value = values.get(field, "")
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", value):
                raise ValueError
            dates[field] = datetime.fromisoformat(value)
        except ValueError:
            errors[field] = f"Enter a valid {label} date and time."
    if "pickup" in dates and dates["pickup"] < datetime.now().replace(second=0, microsecond=0):
        errors["pickup"] = "Pickup date and time cannot be in the past."
    if len(dates) == 2 and dates["return_time"] <= dates["pickup"]:
        errors["return_time"] = "Return must be after pickup."
    return dates, errors


def search_vehicles(database: Path, values: dict[str, str]) -> tuple[list[Vehicle], dict[str, str]]:
    _, errors = validate_dates(values)
    branch = values.get("branch", "").strip()
    if not branch or len(branch) > 80:
        errors["branch"] = "Choose a branch."

    for key in ("vehicle_type", "category", "transmission", "fuel_type"):
        if values.get(key) and values[key] not in CHOICES[key]:
            errors[key] = "Choose a valid option."
    seats = values.get("seats", "").strip()
    if seats and not re.fullmatch(r"(?:[1-9]|1[0-5])", seats):
        errors["seats"] = "Enter a whole number from 1 to 15."
    price = values.get("max_price", "").strip()
    if price and (not re.fullmatch(r"[0-9]{1,4}(?:\.[0-9]{1,2})?", price) or Decimal(price) <= 0):
        errors["max_price"] = "Enter an amount from $0.01 to $9,999.99 with at most two decimal places."
    if errors:
        return [], errors

    clauses = ["retired_at IS NULL", "status = 'available'", "branch = ?",
               "NOT EXISTS (SELECT 1 FROM reservations r WHERE r.vehicle_id = vehicles.id "
               "AND r.status = 'confirmed' AND r.pickup < ? AND r.return_time > ?)"]
    args: list = [branch, values["return_time"], values["pickup"]]
    for key in ("vehicle_type", "category", "transmission", "fuel_type"):
        if values.get(key):
            clauses.append(f"{key} = ?")  # key comes from the fixed list above
            args.append(values[key])
    if seats:
        clauses.append("seats >= ?")
        args.append(int(seats))
    if price:
        clauses.append("daily_rate_cents <= ?")
        args.append(int(Decimal(price) * 100))
    with get_connection(database) as connection:
        rows = connection.execute("SELECT * FROM vehicles WHERE " + " AND ".join(clauses)
                                  + " ORDER BY daily_rate_cents, name COLLATE NOCASE, id", args).fetchall()
    return [Vehicle(**dict(row)) for row in rows], {}
