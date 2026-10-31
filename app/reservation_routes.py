"""Review, confirm, and display customer-owned reservations."""

import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Path, Query, Request
from fastapi.responses import RedirectResponse

from app.accounts import optional_user, page, verify_csrf
from app.reservations import ReservationError, create_reservation, get_quote, get_reservation, money
from app.search import FILTERS

router = APIRouter(prefix="/reservations")


def search_query(params) -> str:
    return urlencode({key: params.get(key, "") for key in FILTERS if params.get(key, "")})


@router.get("/review", include_in_schema=False)
def review(request: Request, vehicle_id: int = Query(ge=1, le=9223372036854775807),
           branch: str = "", pickup: str = "", return_time: str = ""):
    search_params = search_query(request.query_params)
    try:
        quote = get_quote(request.app.state.database_path, vehicle_id, branch, pickup, return_time)
    except ReservationError as error:
        return page(request, "reservation_error.html", message=str(error), search_query=search_params, status_code=409)
    review_url = "/reservations/review?" + urlencode({"vehicle_id": vehicle_id, **dict(request.query_params)})
    return page(request, "reservation_review.html", quote=quote, search_query=search_params,
                review_url=review_url, request_key=secrets.token_urlsafe(32))


@router.post("/confirm", include_in_schema=False)
def confirm(request: Request, vehicle_id: int = Form(..., ge=1, le=9223372036854775807),
            branch: str = Form(""), pickup: str = Form(""), return_time: str = Form(""),
            version: str = Form(""), request_key: str = Form(""), csrf_token: str = Form(""),
            search_params: str = Form("")):
    verify_csrf(request, csrf_token)
    user = optional_user(request)
    if user is None:
        return RedirectResponse("/login?expired=1", status_code=303)
    if user.role != "customer":
        raise HTTPException(status_code=403, detail="Sign in with a customer account to reserve a car.")
    try:
        reference = create_reservation(request.app.state.database_path, user.id, vehicle_id, branch,
                                       pickup, return_time, version, request_key)
    except ReservationError as error:
        return page(request, "reservation_error.html", message=str(error), search_query=search_params, status_code=409)
    return RedirectResponse("/reservations/" + reference, status_code=303)


@router.get("/{reference}", include_in_schema=False)
def confirmation(request: Request, reference: str = Path(max_length=30)):
    user = optional_user(request)
    if user is None:
        return RedirectResponse("/login?expired=1", status_code=303)
    booking = get_reservation(request.app.state.database_path, user.id, reference)
    if booking is None:
        raise HTTPException(status_code=404, detail="Reservation not found.")
    return page(request, "reservation_confirmation.html", booking=booking, format_money=money)
