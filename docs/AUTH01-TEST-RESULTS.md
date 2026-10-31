# AUTH-01 technical verification

Status: implemented and technically verified locally. Product Owner acceptance is pending; this record is not a claim that the Product Owner has accepted the story.

## Automated integration checks

Command: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`

Result: **14 tests passed**, against temporary SQLite databases. These tests do not modify the development accounts.

| Check | Result |
| --- | --- |
| Registration persists a bcrypt hash and always creates a customer, even when a manager role is submitted | Pass |
| Required/invalid fields show associated errors; password is not echoed | Pass |
| Duplicate username/email checks are case-insensitive and use a generic error | Pass |
| Long ASCII and multi-byte passwords exceeding bcrypt's 72-byte limit are rejected cleanly | Pass |
| Login accepts username or email; JWT expiry is exactly 900 seconds; cookie is HttpOnly | Pass |
| Wrong password and unknown account return the same error; oversized login passwords do not crash | Pass |
| Anonymous users and customers cannot access protected staff endpoints/pages | Pass |
| Staff can access the staff page; role changes in SQLite take effect on the next protected request | Pass |
| Expired, forged, malformed-subject, and unsaved-session tokens are rejected | Pass |
| Sign-out removes the cookie and invalidates a copied session token | Pass |
| Missing/invalid CSRF tokens and foreign Origin headers are rejected | Pass |
| Account text is HTML-escaped and private responses are not cached | Pass |
| SQL-like login input cannot bypass authentication | Pass |
| Accounts remain usable through a new application instance; the local signing key persists | Pass |

`pip check` also passed with no broken requirements.

## Browser verification

Browser: Microsoft Edge, headless through Playwright, using a separate temporary database and fictitious accounts.

- Registered a customer using the visible form and checked the account-created confirmation.
- Submitted an incorrect password and checked the generic error.
- Signed in as the customer and checked the customer account page.
- Navigated directly to `/staff` and checked the access-denied page.
- Signed out, signed in as locally provisioned test staff, and checked staff routing.
- Signed out as staff.
- Checked registration and login at 390px and 320px widths: no horizontal overflow.
- Used the keyboard skip link and verified the next tab reaches the first registration field.
- Observed no browser script errors.
- Visually reviewed desktop registration, mobile registration, and the customer account page.

Screenshots contain fictitious test data, not the local staff password:

- [Registration, desktop](screenshots/auth-register-desktop.png)
- [Registration, mobile](screenshots/auth-register-mobile.png)
- [Customer account, desktop](screenshots/auth-account-desktop.png)

The running development app at `http://127.0.0.1:8000/login` was also checked for the updated sign-in page. One local staff account is provisioned; its generated credentials remain in ignored `data/staff-credentials.txt`.

## Review still required

- Product Owner checks the registration/login journey against AUTH-01 acceptance criteria.
- Team members review the implementation and run the tests in their own environments.
- Keyboard checks and responsive screenshots are focused checks, not a complete WCAG audit.
- No claim is made that login abuse protection, email verification, password reset, deployment encryption, or production readiness has been delivered in this local prototype.
- Search, reservations, and fleet management remain outside this implemented feature.
