# UI-01 vehicle search: progress and verification

Status: search and reservation-aware availability are implemented and technically tested. Product Owner acceptance is pending.

## Implemented behavior

Search accepts a branch, pickup and return date/time, optional type, category, maximum daily rate, minimum seats, transmission, and fuel. It shows matching non-removed cars marked available by staff, excluding confirmed reservations that overlap the requested interval. Results are sorted by rate and show daily cost and specifications. The form validates dates and fields, and provides empty and error states. After a search, filter changes update results without a full page reload. A regular GET submission works when JavaScript is unavailable.

## Verification

Command: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`

Result: **52 tests passed**: 9 search checks, 12 reservation checks, and 31 account and fleet checks. Search checks use temporary SQLite databases and cover matching and combined filters, staff availability status, removed cars, branches with no available cars, empty states, invalid and past dates, filters, price/seat boundaries, HTML escaping, and query parameter handling. Reservation checks include overlap and adjacent interval behavior. The past-pickup check uses the server's local timezone and minute precision.

Browser checks used Microsoft Edge and a separate temporary database with fictional cars. They verified search and filter updates without navigation, date errors and correction, responsive layouts at 320px and 390px, and a complete search with JavaScript disabled. No browser script errors were observed.

- [Search results, desktop](screenshots/search-results-desktop.png) (captured before reservations were added)
- [Search results, mobile](screenshots/search-results-mobile.png) (captured before reservations were added)

Booking integration and the approved started-24-hour pricing rule are covered in `UI02-TEST-RESULTS.md`. Product Owner acceptance remains pending. Times use the server's local clock; timezone conversion and daylight-saving transitions are not modeled. Focused responsive checks are not a full accessibility audit.
