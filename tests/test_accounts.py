"""AUTH-01 integration checks against a temporary database, never real accounts."""

from pathlib import Path
import tempfile
import time
import unittest

import bcrypt
from fastapi.testclient import TestClient
import jwt

from app import auth
from app.database import get_connection
from app.main import create_app


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "test.db"
        self.key = "test-signing-key-" * 5
        self.client = TestClient(create_app(self.database, self.key), follow_redirects=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def post(self, path, **fields):
        self.client.get("/login")
        return self.client.post(path, data={"csrf_token": self.client.cookies[auth.CSRF_COOKIE], **fields})

    def register(self, **overrides):
        return self.post("/register", **{
            "name": "Test Customer", "email": "customer@example.test",
            "username": "customer", "password": "TestPassword123!", **overrides,
        })

    def login(self, **overrides):
        return self.post("/login", **{
            "identifier": "customer", "password": "TestPassword123!", **overrides,
        })

    def test_registration_persists_only_a_bcrypt_hash_and_customer_role(self):
        response = self.register(role="manager")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login?registered=1")
        with get_connection(self.database) as connection:
            row = connection.execute("SELECT * FROM users").fetchone()
        self.assertEqual(row["role"], "customer")
        self.assertTrue(row["password_hash"].startswith("$2b$"))
        self.assertTrue(bcrypt.checkpw(b"TestPassword123!", row["password_hash"].encode()))
        self.assertNotIn("TestPassword123!", response.text)

    def test_invalid_fields_show_associated_errors_without_echoing_password(self):
        response = self.register(name="", email="bad", username="x", password="short")
        self.assertEqual(response.status_code, 400)
        for field in ["name", "email", "username", "password"]:
            self.assertIn(f'id="{field}-error"', response.text)
        self.assertNotIn('value="short"', response.text)
        self.assertIn('aria-invalid="true"', response.text)

    def test_duplicate_username_or_email_is_case_insensitive_and_generic(self):
        self.register()
        first = self.register(email="CUSTOMER@example.test", username="another")
        second = self.register(email="other@example.test", username="CUSTOMER")
        for response in [first, second]:
            self.assertEqual(response.status_code, 400)
            self.assertIn("Unable to create an account with those details", response.text)
        with get_connection(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_utf8_password_byte_limit_is_validated(self):
        self.assertEqual(self.register(password="a" * 73).status_code, 400)
        self.assertEqual(self.register(password="\U0001f600" * 19).status_code, 400)

    def test_login_accepts_email_and_username_and_sets_fifteen_minute_session(self):
        self.register()
        for identifier in ["CUSTOMER", "CUSTOMER@EXAMPLE.TEST"]:
            response = self.login(identifier=identifier)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/account")
            cookie = response.headers["set-cookie"]
            self.assertIn("HttpOnly", cookie)
            self.assertIn("Max-Age=900", cookie)
            self.assertIn("SameSite=lax", cookie)
            claims = auth.session_claims(self.client.cookies[auth.SESSION_COOKIE], self.key)
            self.assertEqual(claims["exp"] - claims["iat"], 900)
            self.assertEqual(self.client.get("/account").status_code, 200)
            details = self.client.get("/api/me").json()
            self.assertEqual(details["role"], "customer")
            self.assertNotIn("password_hash", details)

    def test_unknown_account_and_wrong_password_have_same_error(self):
        self.register()
        wrong = self.login(password="wrong")
        unknown = self.login(identifier="unknown", password="wrong")
        for response in [wrong, unknown]:
            self.assertEqual(response.status_code, 400)
            self.assertIn("Incorrect username/email or password.", response.text)
        self.assertNotIn(auth.SESSION_COOKIE, self.client.cookies)
        self.assertEqual(self.login(password="a" * 100).status_code, 400)

    def test_anonymous_and_customer_cannot_access_staff_resources(self):
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.get("/api/staff/access").status_code, 401)
        self.assertEqual(self.client.get("/staff").headers["location"], "/login?expired=1")
        self.register(role="staff")
        self.login()
        self.assertEqual(self.client.get("/staff").status_code, 403)
        self.assertEqual(self.client.get("/api/staff/access").status_code, 403)

    def test_staff_login_and_role_changes_are_checked_against_database(self):
        user = auth.register_user(self.database, "Staff", "staff@example.test", "staff",
                                  "StaffPassword123!", role="staff")
        response = self.login(identifier="staff", password="StaffPassword123!")
        self.assertEqual(response.headers["location"], "/staff")
        self.assertEqual(self.client.get("/staff").status_code, 200)
        self.assertEqual(self.client.get("/api/staff/access").status_code, 200)
        with get_connection(self.database) as connection:
            connection.execute("UPDATE users SET role = 'customer' WHERE id = ?", (user.id,))
        self.assertEqual(self.client.get("/api/staff/access").status_code, 403)

    def test_expired_or_tampered_tokens_and_unregistered_sessions_are_rejected(self):
        self.register()
        self.login()
        token = self.client.cookies[auth.SESSION_COOKIE]
        claims = auth.session_claims(token, self.key)
        for modifications in [{"iat": int(time.time()) - 1000, "exp": int(time.time()) - 1},
                              {"jti": "not-a-saved-session"}, {"sub": "9" * 100}]:
            altered = jwt.encode({**claims, **modifications}, self.key, algorithm="HS256")
            self.client.cookies.set(auth.SESSION_COOKIE, altered)
            self.assertEqual(self.client.get("/api/me").status_code, 401)
        forged = jwt.encode(claims, "wrong-signing-key-" * 5, algorithm="HS256")
        self.client.cookies.set(auth.SESSION_COOKIE, forged)
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.get("/account").status_code, 303)

    def test_logout_revokes_a_copied_token_and_clears_browser_cookie(self):
        self.register()
        self.login()
        token = self.client.cookies[auth.SESSION_COOKIE]
        response = self.post("/logout")
        self.assertEqual(response.status_code, 303)
        self.assertNotIn(auth.SESSION_COOKIE, self.client.cookies)
        self.client.cookies.set(auth.SESSION_COOKIE, token)
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_posts_require_csrf_and_reject_foreign_origins(self):
        for path in ["/register", "/login", "/logout"]:
            self.assertEqual(self.client.post(path, data={}).status_code, 403)
        self.client.get("/login")
        token = self.client.cookies[auth.CSRF_COOKIE]
        response = self.client.post("/login", data={"csrf_token": token}, headers={"Origin": "https://other.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post("/login", data={"csrf_token": "\u00e9"}).status_code, 403)

    def test_account_values_are_escaped_and_private_responses_not_cached(self):
        self.register(name="<script>alert(1)</script>")
        self.login()
        response = self.client.get("/account")
        self.assertIn("&lt;script&gt;", response.text)
        self.assertNotIn("<script>alert(1)</script>", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/api/me").headers["cache-control"], "no-store")

    def test_sql_input_is_not_interpreted_as_a_query(self):
        self.register()
        self.assertEqual(self.login(identifier="' OR 1=1 --").status_code, 400)
        with get_connection(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_accounts_and_signing_key_survive_restart(self):
        self.register()
        with TestClient(create_app(self.database, self.key), follow_redirects=False) as restarted:
            restarted.get("/login")
            response = restarted.post("/login", data={"identifier": "customer", "password": "TestPassword123!",
                                                       "csrf_token": restarted.cookies[auth.CSRF_COOKIE]})
            self.assertEqual(response.status_code, 303)
        first = auth.load_signing_key(Path(self.directory.name))
        self.assertEqual(first, auth.load_signing_key(Path(self.directory.name)))


if __name__ == "__main__":
    unittest.main()
