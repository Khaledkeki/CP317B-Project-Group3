"""Sprint 2 pages: rental history (UI-03), cancellation (UI-04),
daily rental monitoring (ADMIN-02), and the utilization report (REP-01).

Data comes from app.reservations and app.reports.
"""

from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import RedirectResponse

from app import reports
from app.accounts import optional_user, page, require_staff, require_user, verify_csrf
from app.fleet import CHOICES, list_branches
from app.models import User
from app.reservations import (CancellationError, ReturnError, booking_phase, cancel_reservation,
                              get_reservation, money, record_return)

router = APIRouter()


def require_manager(user: User = Depends(require_user)) -> User:
    if user.role != "manager":
        raise HTTPException(status_code=403, detail="Manager access is required.")
    return user


# ---- UI-04: cancel a future reservation -------------------------------------------

def _customer_booking(request: Request, reference: str):
    user = optional_user(request)
    if user is None:
        return None, RedirectResponse("/login?expired=1", status_code=303)
    booking = get_reservation(request.app.state.database_path, user.id, reference)
    if booking is None:
        raise HTTPException(status_code=404, detail="Reservation not found.")
    return booking, None


@router.get("/reservations/{reference}/cancel", include_in_schema=False)
def cancel_review(request: Request, reference: str = Path(max_length=30)):
    booking, redirect = _customer_booking(request, reference)
    if redirect:
        return redirect
    return page(request, "reservation_cancel.html", booking=booking, info=booking_phase(booking),
                format_money=money)


@router.post("/reservations/{reference}/cancel", include_in_schema=False)
def cancel_submit(request: Request, reference: str = Path(max_length=30), csrf_token: str = Form("")):
    verify_csrf(request, csrf_token)
    booking, redirect = _customer_booking(request, reference)
    if redirect:
        return redirect
    try:
        cancel_reservation(request.app.state.database_path, booking["user_id"], reference)
    except CancellationError as error:
        return page(request, "reservation_cancel.html", booking=booking, info=booking_phase(booking),
                    format_money=money, errors={"form": str(error)}, status_code=409)
    return RedirectResponse(f"/account?cancelled={reference}#cancelled", status_code=303)


# ---- ADMIN-02: daily rental monitoring --------------------------------------------

def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else date.today()
    except ValueError:
        return None


@router.get("/staff/daily", include_in_schema=False, dependencies=[Depends(require_staff)])
def daily_rentals(request: Request, branch: str = "", day: str = ""):
    database = request.app.state.database_path
    branches = list_branches(database)
    selected_day = _parse_day(day)
    errors = {} if selected_day else {"day": "Enter a valid date."}
    if branch and branch.casefold() not in {option.casefold() for option in branches}:
        errors["branch"] = "Choose a branch from the list."
    branch = branch or (branches[0] if branches else "")
    activity = (reports.daily_activity(database, branch, selected_day)
                if branch and not errors else {"pickups": [], "active": [], "returns": [], "returned": []})
    day_value = selected_day or date.today()
    return page(request, "daily_rentals.html", branches=branches, branch=branch, day=day_value,
                day_text=day if errors.get("day") else day_value.isoformat(),
                previous_day=(day_value - timedelta(days=1)).isoformat(),
                next_day=(day_value + timedelta(days=1)).isoformat(),
                activity=activity, errors=errors, status_code=400 if errors else 200)


@router.post("/staff/daily/return", include_in_schema=False, dependencies=[Depends(require_staff)])
def daily_return(request: Request, reference: str = Form("", max_length=30), branch: str = Form(""),
                 day: str = Form(""), csrf_token: str = Form("")):
    verify_csrf(request, csrf_token)
    query = {"branch": branch, "day": day}
    try:
        record_return(request.app.state.database_path, reference)
    except ReturnError as error:
        query["error"] = str(error)
    else:
        query["returned"] = reference
    return RedirectResponse("/staff/daily?" + urlencode(query) + "#returned", status_code=303)


# ---- REP-01: vehicle utilization and customer preferences ---------------------------

@router.get("/manager/reports/utilization", include_in_schema=False, dependencies=[Depends(require_manager)])
def utilization(request: Request, branch: str = "", start: str = "", end: str = "", group_by: str = "model"):
    database = request.app.state.database_path
    default_start, default_end = reports.default_report_period()
    errors = {}
    try:
        start_day = date.fromisoformat(start) if start else default_start
    except ValueError:
        start_day, errors["start"] = default_start, "Enter a valid start date."
    try:
        end_day = date.fromisoformat(end) if end else default_end
    except ValueError:
        end_day, errors["end"] = default_end, "Enter a valid end date."
    if not errors and end_day < start_day:
        errors["end"] = "End date must be on or after the start date."
    elif not errors and (end_day - start_day).days > 366:
        errors["end"] = "Choose a period of one year or less."
    if group_by not in reports.REPORT_GROUPS:
        errors["group_by"] = "Choose how to group the report."
        group_by = "model"
    branches = list_branches(database)
    if branch and branch.casefold() not in {option.casefold() for option in branches}:
        errors["branch"] = "Choose a branch from the list."
    rows = [] if errors else reports.utilization_report(database, branch, start_day, end_day, group_by)
    labels = CHOICES.get(group_by, {})
    total = sum(row["rentals"] for row in rows)
    for row in rows:
        row["display"] = labels.get(row["label"], row["label"])
        row["share"] = round(row["rentals"] * 100 / total) if total else 0
    return page(request, "utilization_report.html", branches=branches, branch=branch,
                start=start or start_day.isoformat(), end=end or end_day.isoformat(), group_by=group_by,
                groups=reports.REPORT_GROUPS, rows=rows, total=total,
                total_days=sum(row["rental_days"] for row in rows),
                max_rentals=max((row["rentals"] for row in rows), default=0),
                errors=errors, status_code=400 if errors else 200)
