"""HTML account pages and server-side authorization."""

from dataclasses import asdict
import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import auth
from app.database import PROJECT_DIR
from app.models import User
from app.reservations import list_reservations, money

router = APIRouter()
templates = Jinja2Templates(directory=PROJECT_DIR / "app" / "templates")


def optional_user(request: Request) -> User | None:
    return auth.current_user(request.app.state.database_path, request.app.state.signing_key,
                             request.cookies.get(auth.SESSION_COOKIE, ""))


def require_user(request: Request) -> User:
    user = optional_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    return user


def require_staff(user: User = Depends(require_user)) -> User:
    if user.role not in {"staff", "manager"}:
        raise HTTPException(status_code=403, detail="Staff access is required.")
    return user


def page(request: Request, name: str, *, status_code: int = 200, **context):
    csrf = request.cookies.get(auth.CSRF_COOKIE) or secrets.token_urlsafe(32)
    response = templates.TemplateResponse(
        request=request, name=name, status_code=status_code,
        context={"user": optional_user(request), "csrf_token": csrf, "errors": {}, "values": {}, **context},
    )
    response.set_cookie(auth.CSRF_COOKIE, csrf, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")
    response.headers["Cache-Control"] = "no-store"
    return response


def verify_csrf(request: Request, token: str) -> None:
    cookie = request.cookies.get(auth.CSRF_COOKIE, "")
    origin = request.headers.get("origin")
    if (not cookie or not token or not secrets.compare_digest(cookie.encode(), token.encode())
            or (origin is not None and origin != str(request.base_url).rstrip("/"))):
        raise HTTPException(status_code=403, detail="Your form has expired. Reload the page and try again.")


@router.get("/register", include_in_schema=False)
def registration_page(request: Request):
    return page(request, "register.html")


@router.post("/register", include_in_schema=False)
def register(request: Request, name: str = Form(""), email: str = Form(""),
             username: str = Form(""), password: str = Form(""), csrf_token: str = Form("")):
    verify_csrf(request, csrf_token)
    try:
        # Public registration always creates customers, regardless of submitted role.
        auth.register_user(request.app.state.database_path, name, email, username, password)
    except auth.RegistrationError as error:
        return page(request, "register.html", status_code=400, errors=error.errors,
                    values={"name": name, "email": email, "username": username})
    return RedirectResponse("/login?registered=1", status_code=303)


@router.get("/login", include_in_schema=False)
def login_page(request: Request):
    return page(request, "login.html", next_path=safe_login_next(request.query_params.get("next", "")))


def safe_login_next(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if not parts.scheme and not parts.netloc and parts.path == "/reservations/review" and len(value) <= 4000:
        return value
    return ""


@router.post("/login", include_in_schema=False)
def login(request: Request, identifier: str = Form(""), password: str = Form(""),
          csrf_token: str = Form(""), next_path: str = Form("", alias="next")):
    verify_csrf(request, csrf_token)
    user = auth.authenticate(request.app.state.database_path, identifier, password)
    if user is None:
        return page(request, "login.html", status_code=400,
                    errors={"form": "Incorrect username/email or password."}, values={"identifier": identifier},
                    next_path=safe_login_next(next_path))
    auth.revoke_session(request.app.state.database_path, request.app.state.signing_key,
                        request.cookies.get(auth.SESSION_COOKIE, ""))
    token = auth.create_session(request.app.state.database_path, request.app.state.signing_key, user)
    destination = "/staff" if user.role in {"staff", "manager"} else safe_login_next(next_path) or "/account"
    response = RedirectResponse(destination, status_code=303)
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_SECONDS,
                        httponly=True, secure=request.url.scheme == "https", samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/logout", include_in_schema=False)
def logout(request: Request, csrf_token: str = Form("")):
    verify_csrf(request, csrf_token)
    auth.revoke_session(request.app.state.database_path, request.app.state.signing_key,
                        request.cookies.get(auth.SESSION_COOKIE, ""))
    response = RedirectResponse("/login?logged_out=1", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/account", include_in_schema=False)
def account(request: Request):
    user = optional_user(request)
    if user is None:
        return RedirectResponse("/login?expired=1", status_code=303)
    if user.role in {"staff", "manager"}:
        return RedirectResponse("/staff", status_code=303)
    return page(request, "account.html", user=user,
                reservations=list_reservations(request.app.state.database_path, user.id), format_money=money)


@router.get("/staff", include_in_schema=False)
def staff(request: Request):
    user = optional_user(request)
    if user is None:
        return RedirectResponse("/login?expired=1", status_code=303)
    if user.role not in {"staff", "manager"}:
        return page(request, "forbidden.html", status_code=403, user=user)
    return page(request, "staff.html", user=user)


@router.get("/api/me", tags=["Accounts"])
def me(user: User = Depends(require_user)):
    return asdict(user)


@router.get("/api/staff/access", tags=["Accounts"])
def staff_access(user: User = Depends(require_staff)):
    """Verify staff access; fleet features will use this authorization dependency."""
    return {"authorized": True, "role": user.role}
