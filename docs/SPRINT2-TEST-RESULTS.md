# Sprint 2 backend technical verification

Status: implemented and technically verified locally; Product Owner acceptance is pending.

## Implemented scope

The Sprint 2 placeholder module was removed. Rental history (UI-03), online
cancellation (UI-04), daily rental monitoring with recorded returns (ADMIN-02),
and the utilization report (REP-01) now read and write the SQLite database.

- Rental history groups a customer's own bookings into current, upcoming, past,
  and cancelled. Only upcoming bookings offer cancellation. Completed rentals
  show the recorded return time.
- Cancellation is scoped to the signed-in customer, requires CSRF, and takes the
  SQLite write lock before checking, so two concurrent submissions produce exactly
  one success. Cancelled dates become bookable again.
- Daily rentals group one branch's confirmed bookings per day. A return recorded
  that day takes priority, then a pickup that day, then a scheduled return, then
  a car that is on rent across the day. Staff can record a return from the
  pickup, on-rent, and returns-due tables.
- The utilization report ranks confirmed bookings picked up inside a branch and
  period, grouped by model, vehicle type, or category. Cancelled bookings are
  excluded and no customer details are exposed. Managers only.
- Manager accounts can be provisioned with `python -m app.create_staff --role manager`.

## Schema change and migration

`reservations` gained a nullable `returned_at` column. A status value was not
added because SQLite cannot alter a `CHECK` constraint without rebuilding the
table, and every availability and reporting query filters `status = 'confirmed'`.

Fresh databases get the column from `CREATE TABLE`. Existing databases are
migrated on startup by checking `PRAGMA table_info(reservations)` and running a
single `ALTER TABLE ... ADD COLUMN`. The check and the change run inside
`BEGIN IMMEDIATE`, so several workers starting against the same older database
add the column exactly once. No rows or indexes are dropped.

Two indexes were added: `reservations_customer(user_id, pickup)` and
`reservations_daily(branch, pickup)`.

## Automated checks

Command: `.venv/bin/python -m unittest discover -s tests`

Result: **90 tests passed** (15 authentication, 17 fleet, 9 search,
12 reservations, 11 Sprint 2 pages, 26 Sprint 2 backend).

The new backend suite covers:

| Check | Result |
| --- | --- |
| A recorded return survives a new application instance on the same database | Pass |
| Returns are refused for future pickups, cancelled bookings, unknown references, and repeats; nothing is written on refusal | Pass |
| Cancelled dates can be booked again by another customer | Pass |
| Cancelling a returned booking is refused and leaves the status unchanged | Pass |
| Two concurrent cancels produce one success and one refusal | Pass |
| Cancellation and return are mutually exclusive by lifecycle | Pass |
| A same-day rental is a pickup, then moves to returned when closed | Pass |
| An early return does not reappear on its scheduled return day | Pass |
| A returned booking never appears as on rent | Pass |
| The report excludes cancelled bookings and includes returned ones | Pass |
| Daily activity returns only bookings from the requested branch | Pass |
| A late return is listed on the day it was recorded, not the missed scheduled day | Pass |
| The return endpoint rejects a customer session and writes nothing | Pass |
| An unknown report grouping is rejected before reaching SQL; customers get 403 | Pass |
| The return endpoint rejects a wrong CSRF token and writes nothing | Pass |
| Lifecycle boundaries are inclusive: pickup at the minute is current, return at the minute is past, and the comparison holds across a year rollover | Pass |
| Reports include retired vehicles and match the branch case-insensitively | Pass |
| The report period allows 366 days and rejects 367 | Pass |
| A vehicle name containing HTML is escaped on the account, daily, and report pages | Pass |
| A form post carrying a foreign `Origin` header is rejected with 403 and changes nothing | Pass |
| A booking returned early keeps holding its original dates (documented, conservative) | Pass |
| Cancelling while another customer rebooks the same dates never leaves two confirmed overlapping bookings | Pass |
| Model, vehicle-type, and category grouping agree; period boundaries are inclusive | Pass |
| A returned booking shows as completed with no cancel link | Pass |
| The cancel page refuses a returned booking with a clear message | Pass |
| A legacy database gains `returned_at` with existing rows intact; migration is idempotent | Pass |
| Eight concurrent initialisations of one legacy database add the column exactly once, with no errors | Pass |

The existing authentication, fleet, search, reservation, and Sprint 2 page
checks still pass. `tests/test_sprint2_frontend.py` no longer references the
removed placeholder module.

## Manual verification

A seeded local instance was exercised end to end at
`http://127.0.0.1:8000`:

- Customer history showed one current, one upcoming, two past, and two cancelled
  bookings, then moved a booking to cancelled after confirmation.
- The daily page for Waterloo showed 1 pickup, 1 on rent, 0 returns due, and 1
  returned, with the recorded return persisting across a server restart.
- The utilization report ranked 10 rentals by model and by category with no
  customer names.
- A legacy database upgraded in place matches a freshly created one
  column-for-column, passes `PRAGMA integrity_check`, reports no foreign-key
  violations, and keeps both original and new indexes.
- Twenty separate processes upgrading the same legacy database, repeated three
  times, completed with zero failures; 25 repeated migrations on the same file
  were no-ops.
- A database created by the shipped `b1e6726` schema was migrated in place: all
  14 original columns and the original availability index survived, and
  `returned_at` was added.
- A 14-step headless Chrome acceptance run against an isolated database passed
  end to end: search, select a car, review the quote, confirm, find it under
  upcoming, cancel it, staff records the return, and the manager report reflects
  the rental without exposing customer identity.
- A four-worker `uvicorn` deployment served 8 health checks and 40 authenticated
  account requests without a single failure, and a session created by one worker
  was accepted by the others (shared key and shared session table).
- Six separate processes racing on the same database: exactly one cancel
  succeeded and five were refused; exactly one return succeeded and five were
  refused. No `database is locked` errors.
- An eight-process startup probe found a pre-existing race in
  `load_signing_key`: a process could read `session.key` before the creator had
  written it and fail with "The local session signing key is invalid". Key
  creation now writes a private candidate file and links it into place, so the
  target never exists half-written. The same probe passes 8/8 after the change,
  and a regression test covers concurrent creation.

Screenshots: `docs/screenshots/sprint2/`.

## Known limitations

- Utilization reports read the vehicle's current type and category, so editing a
  vehicle's category also changes historical reports. The vehicle name is
  snapshotted on the reservation; type and category are not.
- Rental days count the full charged days of a booking that starts inside the
  period, even when the rental extends past the period end.
- Timestamps remain local naive strings. Timezone and DST handling are unchanged
  from Sprint 1.
- Recording a return does not change the vehicle's operational status. Status is
  managed manually through fleet management.
- A late return recorded after the scheduled return date appears in the Returned
  group on the day the return was recorded, not on the missed scheduled day.
- A booking returned early still holds its original dates, so the car cannot be
  re-rented for the unused part of that window. This is deliberate: releasing
  dates automatically could double-book a car whose key was handed back early.

## Verification of the pushed artifact

The checks above ran in the working copy. To confirm the branch that was actually
pushed (PR #2, head `3f65c3b`), a fresh clone was made from
`origin/sprint2-backend` into an empty directory and verified independently.

Environment and packaging:

- `python -m venv` then `pip install -r requirements.txt` alone is enough to import
  the application; `requirements-dev.txt` adds only the test client.
- The clone tracked no `data/`, `.venv/`, `__pycache__`, database, or key files.
- With no `data/` directory present, the first start created the database and both
  new indexes.
- `python -m app.dev_seed` populated the demo fleet, and the legacy upgrade path
  added `returned_at` to an old-schema database using the shipped code.

Functional checks against the running clone (two workers, then one after restart):

| Requirement | Check on the pushed branch | Result |
| --- | --- | --- |
| UI-03 | Account shows current, upcoming, past, and cancelled groups; completed rentals show a return time | Pass |
| UI-03 | A returned rental is still reported as completed after a process restart | Pass |
| UI-04 | A booked car is absent from search for its dates before cancellation | Pass |
| UI-04 | Cancelling redirects with a confirmation message and the booking moves to cancelled | Pass |
| UI-04 | The same car reappears in search for those dates after cancellation | Pass |
| ADMIN-02 | Daily page renders for a branch; customer access is refused | Pass |
| ADMIN-02 | Recording a return succeeds and the booking moves to the returned group | Pass |
| ADMIN-02 | The recorded return and its timestamp survive a restart | Pass |
| REP-01 | Report renders for a manager, by model and by category; customer access is 403 | Pass |
| REP-01 | Invalid period and unknown grouping are rejected with 400; no customer names appear | Pass |
| AUTH-01 to UI-02 | Home, search, receipt, `/api/me`, and `/api/staff/access` behave as before | Pass |
| Migration | Old-schema database upgraded in place with its row intact | Pass |
| Suite | `python -m unittest discover -s tests` from the clone: 90 tests | Pass |

All 90 tests and all release checks passed from the fresh clone of the pushed
branch, so the verified behaviour matches what the pull request contains.
