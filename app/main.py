"""FastAPI entry point. Run from the repository with python -m uvicorn app.main:app."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from app.accounts import page, router
from app.auth import load_signing_key
from app.fleet_routes import router as fleet_router
from app.search_routes import router as search_router
from app.reservation_routes import router as reservation_router
from app.database import DATABASE_PATH, PROJECT_DIR, get_connection, initialize_database


def create_app(database_path: Path = DATABASE_PATH, signing_key: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        initialize_database(database_path)
        application.state.signing_key = signing_key or load_signing_key(database_path.parent)
        yield

    application = FastAPI(title="Car Rental System", version="0.3.0", lifespan=lifespan)
    application.state.database_path = database_path
    application.mount("/static", StaticFiles(directory=PROJECT_DIR / "app" / "static"), name="static")
    application.include_router(router)
    application.include_router(fleet_router)
    application.include_router(search_router)
    application.include_router(reservation_router)

    @application.middleware("http")
    async def private_pages(request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request):
        return page(request, "index.html")

    @application.get("/health", tags=["Setup"])
    def health():
        with get_connection(database_path) as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok", "database": "connected"}

    return application


app = create_app()
