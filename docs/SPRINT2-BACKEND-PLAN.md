# Sprint 2 backend plan

Status: plan only. No application code has been changed.
Baseline: `b1e6726` (merge of `sprint2-frontend`), 63 tests passing
(14 auth, 17 fleet, 9 search, 12 reservations, 11 sprint-2 pages).

Owner: Sathursan (backend).
Consumers: Khaled (frontend templates already merged), Iffa (QA / test ownership).

---

## 1. Where the backend actually stands

Sprint 1 is real code. Sprint 2's pages are real, but every Sprint 2 data
function is a stand-in in `app/sprint2_placeholders.py`. That module is imported
by three production files:

| File | Uses |
| --- | --- |
| `app/accounts.py` | `rental_history`, `money` |
| `app/reservation_routes.py` | `booking_phase` |
| `app/sprint2_routes.py` | `booking_phase`, `cancel_reservation`, `daily_activity`, `record_return`, `utilization_report`, `REPORT_GROUPS`, `default_report_period` |
| `tests/test_sprint2_frontend.py` | `sprint2_placeholders._RETURNED.clear()` in `setUp` |

Everything else (auth, sessions, CSRF, fleet, search, reservation creation) was
already production-quality for a prototype: parameterised SQL, `BEGIN IMMEDIATE`
for booking, version checks on vehicle edits, bcrypt, revoked JWT sessions.

So Sprint 2 backend work is **replacing six functions plus one schema change**,
not rebuilding anything.

---

## 2. Verified problems with the placeholders

Each item below was reproduced against the current code, not inferred.

### P1. Returns are lost on restart
`_RETURNED` is a module-level dict. Every recorded return vanishes on process
restart and is invisible to any second worker process. Daily rentals and rental
history are therefore wrong for everyone except the process that recorded it.

### P2. `reservations` has no return state in the schema
`status` is `CHECK (status IN ('confirmed','cancelled'))`. Returns currently
exist only in memory (P1). There is nowhere to persist "this came back at
14:32 on the 15th".

### P3. Daily grouping misplaces same-day rentals
A booking picked up 09:00 and due back 17:00 the same day appears only under
**Pickups** and never under **Returns due**, because the pickup test runs first.
That booking also has no "Record return" button, because `pickups` is rendered
with `show_return=false`. So a same-day rental can never have its return
recorded through the UI.

Reproduced: `daily_activity` returned `{'pickups': [ref]}` and the ref was
absent from `returns`.

### P4. A returned booking is shown as "Returned" on a future day
The `returned` branch tests `return_day == day_text or returned_at[:10] == day_text`.
A car returned today, due back in two days, appears under **Returned** today
**and** on its scheduled return day two days from now.

Reproduced: a booking returned on 2026-11-15 with `return_time` 2026-11-17
appeared in the `returned` group for both dates.

### P5. Cancellation is read-then-write with no write lock
`cancel_reservation` selects, checks, then updates, outside `BEGIN IMMEDIATE`.
Two concurrent cancels both read `confirmed`; the second `UPDATE` matches zero
rows but still returns success. Not corrupting today (the `WHERE status =
'confirmed'` guard saves it), but it silently reports success for a no-op and
is inconsistent with `create_reservation`, which is correctly locked.

### P6. Cancellation does not consider returns
Once returns are persisted, a returned booking would still pass the
`pickup <= now` check in some orderings. Cancellation must also require
`returned_at IS NULL`.

### P7. No migration path
`initialize_database` only runs `CREATE TABLE IF NOT EXISTS`. Any new column
silently does not appear in teammates' existing `data/car_rental.db`. A new
column needs an explicit idempotent `ALTER TABLE`.

### P8. No manager provisioning path
`app/create_staff.py` only creates `role='staff'`, and refuses to run if *any*
staff account exists. `REP-01` is manager-only (`require_manager`), so a real
deployment has no supported way to create the one account that can read the
report. Only `dev_seed.py` creates a manager.

### P9. Report queries have no supporting index
`reservations` has only `(vehicle_id, status, pickup, return_time)`. Daily
rentals filter on `(branch, pickup)` and history filters on `(user_id, pickup)`.

### P10. Tests are coupled to the placeholder
`tests/test_sprint2_frontend.py` calls `sprint2_placeholders._RETURNED.clear()`.
Deleting the module breaks the suite. This is a coordination item with QA, not
a reason to keep the placeholder.

### Known limitations to document, not fix
- `vehicle_type` and `category` in the utilization report come from the
  *current* `vehicles` row, so editing a vehicle rewrites historical reports.
  `vehicle_name` is already snapshotted on the reservation; type and category
  are not. Acceptable for the prototype if written down.
- `rental_days` sums full `charged_days` for bookings picked up inside the
  period, even when the rental extends past the period end.
- Timestamps stay local naive strings. No timezone/DST modelling. Unchanged
  from Sprint 1.
- Vehicle `status` stays a staff-managed field. Recording a return should
  **not** auto-flip `rented → available`; that would fight the manual control
  introduced in ADMIN-01 and clobber `under_repair`. Document the decision.

---

## 3. Target design

### 3.1 Schema

Add one nullable column; do **not** add a `'returned'` status.

```sql
ALTER TABLE reservations ADD COLUMN returned_at TEXT;
```

Why not a status value: SQLite cannot alter a `CHECK` constraint, so
`'returned'` means rebuilding the table. More importantly, every existing query
filters `status = 'confirmed'` for availability, history and reports; adding a
third state would require touching all of them and risks releasing or
double-booking cars. A nullable timestamp is strictly additive: `confirmed` +
`returned_at IS NULL` = on rent; `confirmed` + `returned_at` set = completed.

Indexes to add:

```sql
CREATE INDEX IF NOT EXISTS reservations_customer ON reservations(user_id, pickup);
CREATE INDEX IF NOT EXISTS reservations_daily    ON reservations(branch, pickup);
```

### 3.2 Migration

Update the `CREATE TABLE reservations` block to include `returned_at` for fresh
databases, then in `initialize_database`:

```python
columns = {row["name"] for row in connection.execute("PRAGMA table_info(reservations)")}
if "returned_at" not in columns:
    connection.execute("ALTER TABLE reservations ADD COLUMN returned_at TEXT")
```

Idempotent, no data loss, works on every teammate's existing database.

### 3.3 Module layout

Ponytail-minimal: two modules, one deletion.

```
app/reservations.py   # extend: now_minute, phase, rental_history, booking_phase,
                      #         CancellationError, ReturnError,
                      #         cancel_reservation, record_return
app/reports.py        # new: daily_activity, REPORT_GROUPS,
                      #      utilization_report, default_report_period
app/sprint2_placeholders.py   # deleted
app/sprint2_routes.py  # import from the two modules above; routing logic unchanged
app/accounts.py        # import rental_history from app.reservations
app/reservation_routes.py  # import booking_phase from app.reservations
```

Do not create a service/domain layer. These are query functions over SQLite,
exactly like `fleet.py` and `search.py`.

### 3.4 Behaviour contracts

`phase(row, now)`:

```
cancelled               -> "cancelled"
returned_at is not null -> "past"
return_time <= now      -> "past"
pickup <= now           -> "current"
otherwise               -> "upcoming"
```

`can_cancel` = `phase == "upcoming"`.

`cancel_reservation(database, user_id, reference)`:

1. `BEGIN IMMEDIATE`.
2. Select the row scoped by `user_id` and `reference`. Missing -> "We couldn't
   find that reservation on your account."
3. `status == 'cancelled'` -> "This reservation is already cancelled."
4. `returned_at` set -> "This rental has already been returned."
5. `pickup <= now_minute()` -> "This rental has already started…"
6. `UPDATE … SET status='cancelled' WHERE id=? AND status='confirmed' AND
   returned_at IS NULL`; `rowcount != 1` -> already cancelled.

`record_return(database, reference)`:

1. `BEGIN IMMEDIATE`.
2. Row missing or `status != 'confirmed'` -> error.
3. `returned_at` set -> "This vehicle's return was already recorded."
4. `pickup > now_minute()` -> "This rental hasn't started yet…"
5. `UPDATE … SET returned_at=? WHERE id=? AND returned_at IS NULL`.

`daily_activity(database, branch, day)` — one group per booking, priority
`returned (recorded that day) > pickups (starts that day) > returns (due that
day, not returned) > active`:

- `returned`: `status='confirmed'` and `returned_at[:10] == day`.
- `pickups`: not returned, `pickup[:10] == day`.
- `returns`: not returned, `pickup[:10] != day`, `return_time[:10] == day`.
- `active`: not returned, `pickup[:10] < day < return_time[:10]`.
- Cancelled bookings excluded.

Same-day rentals stay under `pickups` (single-count, so the stat tiles stay
meaningful) and **enable "Record return" on the pickups table** so they can
still be closed out. This is the one template change needed.

`rental_history` grouping is unchanged in shape; `returned_at` now comes from
the database instead of `_RETURNED`.

`utilization_report` behaviour is unchanged except that it excludes nothing new
(cancelled is already excluded by `status='confirmed'`) and gains the index.

---

## 4. Work breakdown

Sequenced. BE-1 blocks everything else.

| ID | Work | Points |
| --- | --- | ---: |
| BE-1 | Schema + migration for `returned_at`; indexes; update `CREATE TABLE`. | 2 |
| BE-2 | Reservation lifecycle in `reservations.py`: `now_minute`, `phase`, `rental_history`, `booking_phase`, `cancel_reservation`, `record_return`, with `BEGIN IMMEDIATE`. | 5 |
| BE-3 | `app/reports.py`: `daily_activity` with the corrected grouping, `utilization_report`, `default_report_period`, `REPORT_GROUPS`. | 3 |
| BE-4 | Rewire `accounts.py`, `reservation_routes.py`, `sprint2_routes.py`; delete `sprint2_placeholders.py`. | 1 |
| BE-5 | Test updates: drop `_RETURNED.clear()`, add the backend cases in §5. | 3 |
| BE-6 | Enable "Record return" on the pickups table (one-line template change). | 1 |
| BE-7 | Manager provisioning: `create_staff.py --role {staff,manager}`, per-role existence check. | 1 |

Total ~16 points. BE-2 and BE-3 can run in parallel after BE-1 if two people
work; BE-4 depends on both. BE-6 and BE-7 are independent.

### Acceptance criteria

- **BE-1**: an existing database created by `b1e6726` gains `returned_at` after
  one startup, keeps all rows, and a fresh database has the column from
  `CREATE TABLE`. Indexes visible in `sqlite_master`.
- **BE-2**: cancel and return each hold the write lock; a concurrent double
  cancel returns exactly one success and one 409; a return recorded in one app
  instance is visible from a second app instance opened on the same file.
- **BE-3**: reproduced cases P3 and P4 return the corrected groups.
- **BE-4**: `grep -r sprint2_placeholders app tests` returns nothing.
- **BE-5**: full suite green; every §5 case covered.
- **BE-6**: a same-day rental shows a Record return button and can be closed.
- **BE-7**: `python -m app.create_staff --role manager` creates a manager on a
  database that already has a staff account.

---

## 5. Test plan

New cases (extend `tests/test_sprint2_frontend.py` for page behaviour, add
`tests/test_sprint2_backend.py` for the data layer):

Persistence / lifecycle
1. Record a return, build a **new** `create_app` on the same database, confirm
   the booking is still `past` with the same `returned_at`.
2. Record a return twice -> second call raises `ReturnError`.
3. Record a return for a future pickup -> `ReturnError`, nothing written.
4. Record a return for a cancelled or unknown reference -> `ReturnError`.
5. Cancel a booking, then re-book the same car and dates -> succeeds.

Concurrency
6. 8 threads POST cancel the same reference -> exactly one 303, the rest 409,
   `status='cancelled'` in the database.
7. Cancel and record-return racing on the same reference -> exactly one
   outcome wins, no row ends up both cancelled and returned.

Daily grouping
8. Same-day pickup+return appears under `pickups`, offers Record return, and
   moves to `returned` on the day the return is recorded.
9. A booking returned early does **not** appear under `returned` on its
   scheduled return day (P4 regression).
10. Returned bookings never appear as `active`.
11. Single-day and multi-day bookings land in exactly one group.

Reports
12. Cancelled and returned bookings: cancelled excluded from the utilization
    report; returned included (it was a real rental).
13. `group_by=vehicle_type` and `category` produce the same totals as `model`.
14. Period boundary: pickup on `start` and on `end` are included;
    `end > start + 366 days` rejected.

History / cancel page
15. Returned booking shows under `past` with the "Returned …" line and no
    Cancel link.
16. Cancelling a returned booking returns 409 with a clear message.

Migration
17. Create a database with the pre-change schema, run `initialize_database`,
    assert the column exists and pre-existing rows are intact.

---

## 6. Coordination and open questions

For Iffa (QA): `tests/test_sprint2_frontend.py` line 20 clears
`sprint2_placeholders._RETURNED`. BE-4 deletes that module, so this line must go
in the same change. Suggest BE-5 ownership sits with QA, paired with the backend
change, so the contract tests are not quietly weakened.

For Khaled (frontend): BE-6 changes `activity_table('pickups', …)` to pass
`show_return=true`. Only template change in this plan.

README is stale: it documents Sprint 1 only, plus `dev_seed.py` and the Sprint 2
templates are missing from the folder-structure block. Documentation task, not
backend.

Questions that need a Product Owner decision before BE-3 is final:

1. **"Returned" group meaning** — "returns recorded on this day" (my
   recommendation, matches the empty-state text) or "bookings whose scheduled
   return date is this day, whether or not they came back". The two differ for
   early and late returns.
2. **Should `record_return` change vehicle status?** Recommendation: no.
   Manual staff control stays authoritative.
3. **Manager account creation** — a CLI flag (BE-7) or leave managers to
   `dev_seed.py` for the prototype?

## 7. Explicitly out of scope

Payments, email/SMS, cancellation fees, timezone/DST handling, admin UI for
managers, JSON APIs for Sprint 2, audit history beyond `returned_at`.
