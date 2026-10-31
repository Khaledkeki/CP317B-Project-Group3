"""Search pages use query parameters so a search can be shared or revisited."""

from fastapi import APIRouter, Request
from app.accounts import page
from app.fleet import CHOICES
from app.search import FILTERS, search_branches, search_vehicles

router = APIRouter()


@router.get("/search", include_in_schema=False)
def search(request: Request):
    values = {key: request.query_params.get(key, "") for key in FILTERS}
    submitted = bool(request.query_params)
    vehicles, errors = search_vehicles(request.app.state.database_path, values) if submitted else ([], {})
    return page(request, "search.html", values=values, errors=errors, vehicles=vehicles,
                submitted=submitted, branches={branch: branch for branch in search_branches(request.app.state.database_path)}, choices=CHOICES,
                status_code=400 if errors else 200)
