# Car Rental System

CP317B - Group 03. Sprint 1 prototype.

## Current status

The prototype provides customer registration and sign-in, staff fleet management, vehicle search, and reservations. Search excludes confirmed bookings that overlap the requested dates. Customers can review estimated costs, confirm a booking, and find their reservation reference in their account. All four Sprint 1 stories are implemented and technically tested; Product Owner acceptance is pending.

## Stack

- Python 3.12 (the version used to verify this setup)
- FastAPI and Uvicorn for the backend/server
- Jinja2 HTML templates and plain CSS; JavaScript will be added where interactions require it
- SQLite through Python's built-in `sqlite3` module

No Node.js installation, external database account, or API key is needed.

## Run locally on Windows (PowerShell)

From the directory where you keep your projects:

```powershell
git clone https://github.com/Khaledkeki/CP317B-Project-Group3.git
cd CP317B-Project-Group3
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

If you already have a local copy, open its folder and start with the virtual-environment step. Calling the virtual environment's Python directly avoids PowerShell activation-policy issues.

Open <http://127.0.0.1:8000>. Stop the server with Ctrl+C. Run the server commands from the repository root.

For subsequent sessions, only the final Uvicorn command is needed. Reinstall requirements when they change.

## Run locally on macOS/Linux

```sh
git clone https://github.com/Khaledkeki/CP317B-Project-Group3.git
cd CP317B-Project-Group3
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload
```

## Verify the setup

With the server running:

1. Open <http://127.0.0.1:8000> and check that the home page loads with styling.
2. Open <http://127.0.0.1:8000/health>. Expected JSON: `{"status":"ok","database":"connected"}`. This endpoint runs a SQLite query.
3. Open <http://127.0.0.1:8000/docs> for the API documentation.
4. Confirm `data/car_rental.db` was created locally when the server started.

The server creates `users`, `sessions`, and `vehicles` tables on startup without deleting existing data. Registration always creates a customer; there is no public staff-role selector. Sign-out revokes the session in SQLite as well as clearing the cookie. Forms include CSRF protection; private responses use `Cache-Control: no-store`.

## Try registration and login

1. Visit <http://127.0.0.1:8000/register> and enter a name, email, username, and password. Use fictitious details for this prototype.
2. Passwords require at least 8 characters and at most 72 UTF-8 bytes (the bcrypt input limit). Usernames allow 3-30 letters, numbers, or underscores and are case-insensitive.
3. Sign in at <http://127.0.0.1:8000/login> using your username or email.
4. A customer lands on `/account`. `/staff` and `/api/staff/access` reject customer access.
5. Use Sign out to end the session. An expired session requires a fresh sign-in; activity does not extend the original 15-minute expiry.

## Try fleet management

1. Sign in with the local staff account and choose **Manage fleet**, or visit <http://127.0.0.1:8000/staff/fleet>.
2. Choose **Add vehicle**. Enter a unique fleet ID (for example `CAR-001`), vehicle name, branch, daily rate, type, category, seats, transmission, fuel, and operational status.
3. Choose **Edit details** to change a saved vehicle. Rates must be $0.01-$9,999.99 with no more than two decimal places; seats must be a whole number from 1 to 15.
4. Filter by branch or operational status: available, rented, or under repair.
5. Choose **Remove**, review the vehicle, and confirm **Remove from fleet**. **Keep vehicle** cancels the action. Removal keeps the record and hides it from the default active fleet list; select **Removed vehicles** to inspect retained records. Choose **Restore vehicle** to return a car to the fleet with its previous operational status. Fleet IDs are not reused.

Existing open edit forms cannot overwrite more recent changes: reload saved details if a conflict is reported. All fleet reads and writes require a current staff/manager session, and all writes require the form's CSRF token. No sample vehicles are added automatically to your development database.

Removal retains vehicle IDs for historical references. Existing reservation snapshots preserve their original vehicle name, branch, dates, and price.

## Try vehicle search

1. Add or restore a vehicle in staff fleet management and set its status to **Available**.
2. Open <http://127.0.0.1:8000/search>. Choose its branch and pickup/return dates and times, then select **Search cars**.
3. Filter by type, category, maximum daily rate, minimum seats, transmission, or fuel. Results update without a page reload after the first search. The form still works with JavaScript disabled.

Search shows cars marked Available by staff that have no overlapping confirmed reservation. Pickup times before the current minute in the server's local timezone are rejected. Adjacent intervals are permitted: a new pickup may start when the previous rental ends.

## Try a reservation

1. Search for an available car, then select **Select car**. Sign in as a customer if prompted.
2. Review the car, branch, dates, daily rate, charged days, and estimated total. Each started 24-hour period is charged as one day; calculations use local server time.
3. Select **Confirm reservation**. The server checks the saved rate and availability again, saves the booking, and shows its reference.
4. Open **My reservations** to see the booking. Search for the same dates to verify that car no longer appears.

Search and booking use local server time; timezone conversion and daylight-saving transitions are not modeled. The prototype collects no payment. Cancellation, separate rental-history management, and reporting remain later-sprint features. Use fictitious data and coordinate before entering test bookings into a shared database.

## Create a staff account locally

From a separate terminal in the repository root:

```powershell
.\.venv\Scripts\python.exe -m app.create_staff
```

The command prompts for details and a password without echoing the password. It creates the local prototype's staff account only if one does not already exist. Staff sign-in uses the same `/login` page and redirects to `/staff`. Staff creation is a local administrative action, never a public registration option.

An initial staff account was provisioned on this development machine. Its generated credentials are in the ignored local file `data/staff-credentials.txt`. Each teammate's database is separate: use the command above on another machine. Do not commit or share the local credentials file publicly.

`data/session.key` is generated locally and persists across server restarts. It signs sessions and is excluded from Git. These settings are for local development; HTTPS deployments use secure cookies and require a deployment review before using real customer data.

## Run automated tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

On macOS/Linux, use `.venv/bin/python` instead. Tests use temporary databases and do not modify your local data. The suite has 14 authentication, 17 fleet, 9 search, and 12 reservation checks. See the files in `docs/` for technical verification and pending Product Owner review.

If port 8000 is occupied, use `--port 8001` and open the equivalent URLs on port 8001.

## Folder structure

```text
app/
  __init__.py
  main.py                # Application factory, home, health, and startup
  accounts.py            # Registration/login pages and authorization dependencies
  auth.py                # Validation, hashing, and session operations
  create_staff.py        # Local staff provisioning command
  fleet.py               # Vehicle validation, persistence, and record retention
  fleet_routes.py        # Staff-only inventory, add/edit, and removal pages
  search.py              # Search validation and vehicle filtering
  search_routes.py       # Search page
  reservations.py        # Pricing, booking transaction, and customer-owned queries
  reservation_routes.py  # Review, confirmation, and receipt pages
  database.py            # SQLite connections and database initialization
  models.py              # Public user and vehicle models
  templates/
    base.html            # Shared HTML layout
    index.html           # Initial home page
  static/
    css/styles.css       # Responsive styles
data/                    # Local database, signing key, credentials; excluded from Git
tests/                   # Authentication and fleet integration checks
docs/                    # Technical verification record
requirements.txt         # Pinned Python dependencies
requirements-dev.txt     # Additional test dependencies
.gitignore
README.md
```

## Sprint 1 scope

| Story | Feature | Points | Status |
| --- | --- | ---: | --- |
| AUTH-01 | Secure registration and login | 3 | Implemented; Product Owner review pending |
| UI-01 | Car search and filtering | 5 | Implemented; Product Owner review pending |
| UI-02 | Car reservations | 8 | Implemented; Product Owner review pending; payment deferred |
| ADMIN-01 | Fleet inventory management | 5 | Implemented; Product Owner review pending |

Source: the team's Milestone 2 product backlog. Further feature work requires review before implementation. Payment integration remains a scope decision to resolve with the Product Owner.

## Team

| Member | Role |
| --- | --- |
| Sathursan Parangkunran | Product Owner; backend development |
| Khaled Ibrahim | Frontend Developer |
| Iffa Faisal | Quality Assurance Lead |
| Arushi Mishra | Requirements Engineer |

Keep `.venv/`, local database files, and secrets out of Git. Use fictitious data for the prototype.
