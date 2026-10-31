# ADMIN-01 fleet management technical verification

Status: implemented and technically verified locally; Product Owner acceptance is pending. No GitHub push has been made.

## Implemented scope

Staff/manager sessions can add and edit vehicles, set rates and branches, record available/rented/under-repair status, filter the inventory, and remove a vehicle through a confirmation screen. Removal retains the vehicle ID and original record and excludes it from the default active fleet list. Staff can restore a removed vehicle with its previous operational status; fleet IDs cannot be reused.

Vehicle fields: unique fleet ID, make/model name, branch, type, category, seats, transmission, fuel, daily rate, and operational status. Rates are stored as integer cents. Edit/removal forms check a record version to prevent a stale form from overwriting newer changes.

## Automated checks

Command: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`

Result: **31 tests passed: 17 fleet checks and all 14 existing authentication checks.** Tests run against temporary databases.

| Fleet check | Result |
| --- | --- |
| Staff creates a vehicle with an exact cent value, visible in inventory and retained through database initialization | Pass |
| Anonymous users and customers cannot read fleet forms or submit mutations; role changes take effect immediately | Pass |
| Missing fields and unsupported choices produce field-linked errors | Pass |
| Negative/zero/non-finite/over-precision/oversized rates and invalid seat counts are rejected | Pass |
| $0.01, $10.10, and $9,999.99 persist exactly | Pass |
| Fleet IDs are unique without regard to case | Pass |
| Staff edits branch, rate, and all three operational statuses | Pass |
| Duplicate IDs and invalid edits preserve previously saved records | Pass |
| Branch/status filters and empty states work | Pass |
| Confirmation GET does not remove a car; confirmed removal preserves a test-only linked historical reference; repeated submission is safe; removed records are read-only | Pass |
| Stale edit/removal forms cannot overwrite newer data | Pass |
| Restoring a removed vehicle retains its details, status, ID, and linked records; restore requires staff access, CSRF, and the current record version | Pass |
| All mutations require valid CSRF tokens and reject foreign Origin headers | Pass |
| Expired sessions cannot add a vehicle | Pass |
| Unknown IDs return 404; IDs outside SQLite's integer range are rejected | Pass |
| Saved text is HTML-escaped; branch filters use bound query parameters; private results are not cached | Pass |

## Browser review

Microsoft Edge through Playwright, with fictitious accounts/vehicles in a separate temporary database:

- Signed in as staff and followed **Manage fleet**.
- Added a Toyota Corolla and checked the saved message and inventory card.
- Opened its prefilled edit form; changed its branch, rate, and status; checked the persisted values.
- Applied branch/status filters and cleared them.
- Opened removal confirmation, chose **Keep vehicle**, and verified the vehicle remained.
- Confirmed removal, checked it disappeared from the default inventory, and found its retained record under **Removed vehicles**. The restore option was added afterward and verified in automated tests.
- Checked inventory and add forms at 390px and 320px widths: no horizontal overflow.
- Checked keyboard navigation through the skip link, return link, and first form field. No browser script errors were observed.
- Visually reviewed desktop inventory/edit forms and mobile inventory/add forms.

Review screenshots contain test data only:

- [Fleet inventory, desktop](screenshots/fleet-inventory-desktop.png)
- [Vehicle edit form, desktop](screenshots/fleet-edit-desktop.png)
- [Fleet inventory, mobile](screenshots/fleet-inventory-mobile.png)
- [Add vehicle, mobile](screenshots/fleet-add-mobile.png)

No browser-test vehicles were inserted into the development database. The existing accounts and signing key are retained.

## Remaining review and scope

The Product Owner still needs to review ADMIN-01. This feature does not deliver customer search, live date availability, actual reservations, or booking cancellation. Preserving a test-only foreign-key reference establishes record retention; it does not establish that the future booking workflow has been implemented or tested. Focused keyboard/mobile checks are not a complete accessibility audit.
