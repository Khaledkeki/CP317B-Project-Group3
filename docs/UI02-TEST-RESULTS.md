# UI-02 reservation technical verification

Status: reservation and date-based search availability implemented and technically tested. Product Owner review is pending. Payment collection and tokenization are deferred.

## Implemented behavior

Customers select a car from search, review vehicle, branch, date/time, daily rate, charged days, and estimated total, then sign in and confirm. Each started 24-hour period counts as one day. A confirmed booking gets a reference and appears in the customer's account. Staff-only accounts cannot book, and receipts are visible only to the booking customer. Login returns a customer to their selected review page.

Availability, the fleet status, and current price/version are checked under a SQLite write lock before a booking is saved. Overlapping confirmed reservations are rejected; a pickup exactly at the previous return time is allowed. A repeated submission with the same request key returns the existing reference without creating a duplicate. Vehicle, branch, and rate are saved as snapshots so later fleet edits do not rewrite old booking details. No card or payment data is collected.

## Automated verification

Command: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`

Result: **52 tests passed**, including 12 reservation checks and all 40 existing authentication, fleet, and search checks, using temporary databases.

Reservation checks cover the end-to-end flow; exact and partial 24-hour pricing; overlapping and adjacent intervals; duplicate submission; two simultaneous customers attempting an overlapping booking; changed prices and fleet details; retained booking snapshots; customer privacy; CSRF; staff restrictions; safe return after sign-in; server-authoritative pricing; and stale availability errors.

## Browser verification

Microsoft Edge through Playwright with fictional customers and a separate temporary fleet: search, select a car, sign in and return to review, inspect a 25-hour/two-day quote, confirm, view the reference and account listing, and verify the car is excluded from an overlapping search. The 320px review page was checked for horizontal overflow and no browser script errors were observed.

- [Review reservation, desktop](screenshots/booking-review-desktop.png)
- [Review reservation, mobile](screenshots/booking-review-mobile.png)
- [Confirmation, desktop](screenshots/booking-confirmation-desktop.png)

## Remaining review and limitations

Product Owner acceptance is pending. The prototype uses server-local, naive date/times: timezone conversion and daylight-saving changes are not modeled. Payment integration, tokenization, cancellation, and the complete rental-history feature remain outside this implementation. Browser and keyboard checks are not a complete accessibility audit.
