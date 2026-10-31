"""Staff-only fleet pages. Every read and write uses server-side authorization."""

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import RedirectResponse

from app.accounts import page, require_staff, verify_csrf
from app import fleet

router = APIRouter(prefix="/staff", dependencies=[Depends(require_staff)])


def vehicle_form(fleet_code: str = Form(""), name: str = Form(""), branch: str = Form(""),
                 vehicle_type: str = Form(""), category: str = Form(""), seats: str = Form(""),
                 transmission: str = Form(""), fuel_type: str = Form(""),
                 daily_rate: str = Form(""), status: str = Form(""),
                 version: str = Form(""), csrf_token: str = Form("")) -> dict:
    return locals()


def existing_vehicle(request: Request, vehicle_id: int):
    vehicle = fleet.get_vehicle(request.app.state.database_path, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found.")
    return vehicle


@router.get("/fleet", include_in_schema=False)
def inventory(request: Request, branch: str = "", status: str = ""):
    if status and status not in {*fleet.CHOICES["status"], "removed"}:
        raise HTTPException(status_code=400, detail="Choose a valid vehicle status.")
    database = request.app.state.database_path
    return page(request, "fleet.html", vehicles=fleet.list_vehicles(database, branch, status),
                branches=fleet.list_branches(database), branch=branch, status=status, choices=fleet.CHOICES)


@router.get("/vehicles/new", include_in_schema=False)
def new_vehicle(request: Request):
    return page(request, "vehicle_form.html", vehicle=None, values={"status": "available"}, choices=fleet.CHOICES)


@router.post("/vehicles/new", include_in_schema=False)
def create_vehicle(request: Request, values: dict = Depends(vehicle_form)):
    verify_csrf(request, values["csrf_token"])
    try:
        fleet.save_vehicle(request.app.state.database_path, values)
    except fleet.VehicleError as error:
        return page(request, "vehicle_form.html", vehicle=None, choices=fleet.CHOICES,
                    values=values, errors=error.errors, status_code=error.status_code)
    return RedirectResponse("/staff/fleet?saved=1", status_code=303)


@router.get("/vehicles/{vehicle_id}/edit", include_in_schema=False)
def edit_vehicle(request: Request, vehicle_id: int = Path(ge=1, le=9223372036854775807)):
    vehicle = existing_vehicle(request, vehicle_id)
    if vehicle.retired_at:
        raise HTTPException(status_code=409, detail="Removed vehicles cannot be edited.")
    return page(request, "vehicle_form.html", vehicle=vehicle, values=vehicle.form_values(), choices=fleet.CHOICES)


@router.post("/vehicles/{vehicle_id}/edit", include_in_schema=False)
def update_vehicle(request: Request, values: dict = Depends(vehicle_form),
                   vehicle_id: int = Path(ge=1, le=9223372036854775807)):
    verify_csrf(request, values["csrf_token"])
    vehicle = existing_vehicle(request, vehicle_id)
    try:
        fleet.save_vehicle(request.app.state.database_path, values, vehicle_id)
    except fleet.VehicleError as error:
        return page(request, "vehicle_form.html", vehicle=vehicle, choices=fleet.CHOICES,
                    values=values, errors=error.errors, status_code=error.status_code)
    return RedirectResponse("/staff/fleet?saved=1", status_code=303)


@router.get("/vehicles/{vehicle_id}/remove", include_in_schema=False)
def removal_confirmation(request: Request, vehicle_id: int = Path(ge=1, le=9223372036854775807)):
    vehicle = existing_vehicle(request, vehicle_id)
    if vehicle.retired_at:
        return RedirectResponse("/staff/fleet?status=removed", status_code=303)
    return page(request, "vehicle_remove.html", vehicle=vehicle)


@router.post("/vehicles/{vehicle_id}/remove", include_in_schema=False)
def remove_vehicle(request: Request, version: str = Form(""), csrf_token: str = Form(""),
                   vehicle_id: int = Path(ge=1, le=9223372036854775807)):
    verify_csrf(request, csrf_token)
    vehicle = existing_vehicle(request, vehicle_id)
    try:
        fleet.retire_vehicle(request.app.state.database_path, vehicle_id, version)
    except fleet.VehicleError as error:
        return page(request, "vehicle_remove.html", vehicle=vehicle,
                    errors=error.errors, status_code=error.status_code)
    return RedirectResponse("/staff/fleet?removed=1", status_code=303)


@router.post("/vehicles/{vehicle_id}/restore", include_in_schema=False)
def restore_vehicle(request: Request, version: str = Form(""), csrf_token: str = Form(""),
                    vehicle_id: int = Path(ge=1, le=9223372036854775807)):
    verify_csrf(request, csrf_token)
    existing_vehicle(request, vehicle_id)
    database = request.app.state.database_path
    try:
        fleet.restore_vehicle(database, vehicle_id, version)
    except fleet.VehicleError as error:
        return page(request, "fleet.html", vehicles=fleet.list_vehicles(database, status="removed"),
                    branches=fleet.list_branches(database), branch="", status="removed", choices=fleet.CHOICES,
                    errors=error.errors, status_code=error.status_code)
    return RedirectResponse("/staff/fleet?restored=1", status_code=303)
