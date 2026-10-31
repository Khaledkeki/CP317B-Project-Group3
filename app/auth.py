"""Account validation, bcrypt passwords, and revocable 15-minute JWT sessions."""

from pathlib import Path
import re
import secrets
import sqlite3
import time

import bcrypt
import jwt

from app.database import get_connection
from app.models import User

SESSION_SECONDS = 15 * 60
SESSION_COOKIE = "rental_session"
CSRF_COOKIE = "rental_csrf"
ISSUER = "cp317-car-rental"
DUMMY_HASH = bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt())


class RegistrationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("Account registration failed")


def load_signing_key(directory: Path) -> str:
    """Persist a random local key in the ignored data directory."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "session.key"
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    key = path.read_text(encoding="ascii").strip()
    if len(key) < 64:
        raise RuntimeError("The local session signing key is invalid.")
    return key


def public_user(row: sqlite3.Row) -> User:
    return User(**{field: row[field] for field in ("id", "name", "email", "username", "role")})


def register_user(database: Path, name: str, email: str, username: str,
                  password: str, *, role: str = "customer") -> User:
    name, email, username = name.strip(), email.strip().lower(), username.strip().lower()
    errors = {}
    if not 1 <= len(name) <= 100 or any(ord(char) < 32 for char in name):
        errors["name"] = "Enter your full name (up to 100 characters)."
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        errors["email"] = "Enter a valid email address."
    if not re.fullmatch(r"[a-z0-9_]{3,30}", username):
        errors["username"] = "Use 3-30 letters, numbers, or underscores."
    if len(password) < 8 or len(password.encode("utf-8")) > 72:
        errors["password"] = "Use at least 8 characters and no more than 72 UTF-8 bytes."
    if errors:
        raise RegistrationError(errors)
    if role not in {"customer", "staff", "manager"}:
        raise ValueError("Invalid account role")
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    try:
        with get_connection(database) as connection:
            cursor = connection.execute(
                "INSERT INTO users (name, email, username, password_hash, role) VALUES (?, ?, ?, ?, ?)",
                (name, email, username, password_hash, role),
            )
            return public_user(connection.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone())
    except sqlite3.IntegrityError:
        raise RegistrationError({"form": "Unable to create an account with those details. Try different details or sign in."}) from None


def authenticate(database: Path, identifier: str, password: str) -> User | None:
    with get_connection(database) as connection:
        row = connection.execute("SELECT * FROM users WHERE username = ? OR email = ?",
                                 (identifier.strip().lower(), identifier.strip().lower())).fetchone()
    candidate = password.encode("utf-8")
    in_bounds = 0 < len(candidate) <= 72
    encoded = row["password_hash"].encode("ascii") if row else DUMMY_HASH
    matches = bcrypt.checkpw(candidate if in_bounds else b"invalid", encoded)
    return public_user(row) if row and in_bounds and matches else None


def create_session(database: Path, key: str, user: User) -> str:
    now = int(time.time())
    session_id = secrets.token_urlsafe(32)
    expires = now + SESSION_SECONDS
    with get_connection(database) as connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (session_id, user.id, expires))
    return jwt.encode({"sub": str(user.id), "jti": session_id, "iat": now,
                       "exp": expires, "iss": ISSUER}, key, algorithm="HS256")


def session_claims(token: str, key: str) -> dict | None:
    try:
        claims = jwt.decode(token, key, algorithms=["HS256"], issuer=ISSUER,
                            options={"require": ["sub", "jti", "iat", "exp", "iss"]})
        if (not isinstance(claims["sub"], str) or not claims["sub"].isascii()
                or not claims["sub"].isdigit() or len(claims["sub"]) > 19
                or not 0 < int(claims["sub"]) <= 9223372036854775807):
            return None
        if not isinstance(claims["jti"], str) or len(claims["jti"]) > 100:
            return None
        return claims
    except jwt.InvalidTokenError:
        return None


def current_user(database: Path, key: str, token: str) -> User | None:
    claims = session_claims(token, key)
    if not claims:
        return None
    with get_connection(database) as connection:
        row = connection.execute(
            "SELECT users.* FROM users JOIN sessions ON users.id = sessions.user_id "
            "WHERE sessions.id = ? AND users.id = ? AND sessions.expires_at > ?",
            (claims["jti"], int(claims["sub"]), int(time.time())),
        ).fetchone()
    return public_user(row) if row else None


def revoke_session(database: Path, key: str, token: str) -> None:
    claims = session_claims(token, key)
    if claims:
        with get_connection(database) as connection:
            connection.execute("DELETE FROM sessions WHERE id = ?", (claims["jti"],))
