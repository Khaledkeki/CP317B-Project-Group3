"""Vehicle persistence and validation. Removal preserves the original record."""

from decimal import Decimal
from pathlib import Path
import re
import sqlite3

from app.database import get_connection
from app.models import Vehicle

CHOICES = {
    "vehicle_type": {"sedan": "Sedan", "suv": "SUV", "hatchback": "Hatchback", "coupe": "Coupe", "minivan": "Minivan", "truck": "Truck"},
    "category": {"economy": "Economy", "standard": "Standard", "premium": "Premium", "luxury": "Luxury"},
    "transmission": {"automatic": "Automatic", "manual": "Manual"},
    "fuel_type": {"gasoline": "Gasoline", "diesel": "Diesel", "hybrid": "Hybrid", "electric": "Electric"},
    "status": {"available": "Available", "rented": "Rented", "under_repair": "Under repair"},
}
FIELDS = ("fleet_code", "name", "branch", "vehicle_type", "category", "seats",
          "transmission", "fuel_type", "daily_rate_cents", "status")


class VehicleError(ValueError):
    def __init__(self, errors: dict[str, str], status_code: int = 400):
        self.errors, self.status_code = errors, status_code
        super().__init__("Vehicle could not be saved")


def validate_vehicle(values: dict) -> dict:
    cleaned = {key: str(values.get(key, "")).strip() for key in FIELDS if key != "daily_rate_cents"}
    errors = {}
    cleaned["fleet_code"] = cleaned["fleet_code"].upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,29}", cleaned["fleet_code"]):
        errors["fleet_code"] = "Use 2-30 letters, numbers, or hyphens, starting with a letter or number."
    for key, limit, label in [("name", 100, "vehicle name"), ("branch", 80, "branch")]:
        if not 1 <= len(cleaned[key]) <= limit or any(ord(char) < 32 for char in cleaned[key]):
            errors[key] = f"Enter a {label} (up to {limit} characters)."
    for key, options in CHOICES.items():
        if cleaned[key] not in options:
            errors[key] = "Choose one of the listed options."
    if not re.fullmatch(r"(?:[1-9]|1[0-5])", cleaned["seats"]):
        errors["seats"] = "Enter a whole number of seats from 1 to 15."
    else:
        cleaned["seats"] = int(cleaned["seats"])
    rate = str(values.get("daily_rate", "")).strip()
    if not re.fullmatch(r"[0-9]{1,4}(?:\.[0-9]{1,2})?", rate) or Decimal(rate) <= 0:
        errors["daily_rate"] = "Enter a daily rate from $0.01 to $9,999.99, with at most two decimal places."
    else:
        cleaned["daily_rate_cents"] = int(Decimal(rate) * 100)
    if errors:
        raise VehicleError(errors)
    return cleaned


def get_vehicle(database: Path, vehicle_id: int) -> Vehicle | None:
    with get_connection(database) as connection:
        row = connection.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    return Vehicle(**dict(row)) if row else None


def list_vehicles(database: Path, branch: str = "", status: str = "") -> list[Vehicle]:
    conditions = ["retired_at IS NOT NULL" if status == "removed" else "retired_at IS NULL"]
    parameters = []
    if branch:
        conditions.append("branch = ?")
        parameters.append(branch)
    if status and status != "removed":
        conditions.append("status = ?")
        parameters.append(status)
    query = "SELECT * FROM vehicles WHERE " + " AND ".join(conditions) + " ORDER BY name COLLATE NOCASE, fleet_code"
    with get_connection(database) as connection:
        return [Vehicle(**dict(row)) for row in connection.execute(query, parameters)]


def list_branches(database: Path) -> list[str]:
    with get_connection(database) as connection:
        return [row[0] for row in connection.execute("SELECT DISTINCT branch FROM vehicles ORDER BY branch COLLATE NOCASE")]


def save_vehicle(database: Path, values: dict, vehicle_id: int | None = None) -> Vehicle:
    cleaned = validate_vehicle(values)
    try:
        with get_connection(database) as connection:
            if vehicle_id is None:
                cursor = connection.execute(
                    "INSERT INTO vehicles (" + ", ".join(FIELDS) + ") VALUES (" + ", ".join("?" for _ in FIELDS) + ")",
                    tuple(cleaned[field] for field in FIELDS),
                )
                vehicle_id = cursor.lastrowid
            else:
                version = str(values.get("version", ""))
                if not re.fullmatch(r"[0-9]{1,18}", version):
                    raise VehicleError({"form": "Reload the vehicle before saving changes."}, 409)
                cursor = connection.execute(
                    "UPDATE vehicles SET " + ", ".join(field + " = ?" for field in FIELDS)
                    + ", version = version + 1 WHERE id = ? AND version = ? AND retired_at IS NULL",
                    (*[cleaned[field] for field in FIELDS], vehicle_id, int(version)),
                )
                if cursor.rowcount != 1:
                    raise VehicleError({"form": "This vehicle changed or was removed while you were editing. Reload its saved details before trying again."}, 409)
            row = connection.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
            return Vehicle(**dict(row))
    except sqlite3.IntegrityError:
        raise VehicleError({"fleet_code": "That fleet ID already exists, including removed vehicles. Use a unique fleet ID."}) from None


def retire_vehicle(database: Path, vehicle_id: int, version: str) -> None:
    if not re.fullmatch(r"[0-9]{1,18}", version):
        raise VehicleError({"form": "Reload the vehicle before removing it."}, 409)
    with get_connection(database) as connection:
        cursor = connection.execute(
            "UPDATE vehicles SET retired_at = CURRENT_TIMESTAMP, version = version + 1 "
            "WHERE id = ? AND version = ? AND retired_at IS NULL", (vehicle_id, int(version)),
        )
        if cursor.rowcount == 0:
            row = connection.execute("SELECT retired_at FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
            if row is None or row["retired_at"] is None:
                raise VehicleError({"form": "This vehicle changed while you were reviewing it. Reload before removing it."}, 409)


def restore_vehicle(database: Path, vehicle_id: int, version: str) -> None:
    if not re.fullmatch(r"[0-9]{1,18}", version):
        raise VehicleError({"form": "Reload the removed vehicles list before restoring."}, 409)
    with get_connection(database) as connection:
        result = connection.execute(
            "UPDATE vehicles SET retired_at = NULL, version = version + 1 "
            "WHERE id = ? AND version = ? AND retired_at IS NOT NULL",
            (vehicle_id, int(version)),
        )
        if result.rowcount != 1:
            raise VehicleError({"form": "This vehicle changed. Reload the removed vehicles list before trying again."}, 409)
