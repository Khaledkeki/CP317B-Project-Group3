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
single `ALTER TABLE ... ADD COLUMN`. No rows or indexes are dropped.

Two indexes were added: `reservations_customer(user_id, pickup)` and
`reservations_daily(branch, pickup)`.

## Automated checks

Command: `.venv/bin/python -m unittest discover -s tests`

Result: **85 tests passed** (15 authentication, 17 fleet, 9 search,
12 reservations, 11 Sprint 2 pages, 21 Sprint 2 backend).

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
| Model, vehicle-type, and category grouping agree; period boundaries are inclusive | Pass |
| A returned booking shows as completed with no cancel link | Pass |
| The cancel page refuses a returned booking with a clear message | Pass |
| A legacy database gains `returned_at` with existing rows intact; migration is idempotent | Pass |

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
- A database created by the shipped `b1e6726` schema was migrated in place: all
  14 original columns and the original availability index survived, and
  `returned_at` was added.
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
