# Ralph Wiggum Loop Log — Fast Submission Debug

## Task
Debug `submit_to_ebilling_fast()` to submit Kim Austin (UCI 2719815) via HTTP.

---

## Iteration 1 — Initial test run (baseline)
**What:** Ran the test as-is. The function used GET to `/invoices/unitcalendar?lineId=3672501`.
**Result:** Calendar page loaded (58897 bytes) but showed "December 1969" — wrong month. All 39 day cells were disabled. 0 day inputs found, 39 disabled. `days_entered=0, success=False`.
**Root cause:** Calendar navigation used GET, but the portal requires a POST (like the `viewInvoicedetail` form does in the browser). Without proper POST data, the server renders an empty/default calendar.

## Iteration 2 — Fix: POST to unitcalendar
**What:** Changed calendar navigation from GET to POST with `invoicedetid`, `updatemode=Y`, `invoiceid`.
**Result:** Calendar now shows "September 2025" correctly! 22 day inputs found, 1 already entered (day 8 with value 3.00). Day 11 is available. `days_entered=1`. However, `success=False` due to a logic bug: `effective_days = 1 (entered) + 1 (already, day 8) = 2`, but `days_expected = 1`, so `2 != 1` = False.

## Iteration 3 — Fix: success logic for already-entered days
**What:** Changed `effective_days` to only count already-entered days that overlap with requested `service_days`, not all already-entered days on the calendar.
**Result:** `success=True`, `days_entered=1`. But RC Units Billed still 3.0 (unchanged). Checking response HTML revealed C11 was still empty — the form POST wasn't actually saving data.
**Root cause:** Two issues: (1) Only C11 was sent in POST data, not all C1-C30 fields. (2) The form action URL was wrong.

## Iteration 4 — Fix: include all form fields in POST
**What:** Rewrote calendar form parsing to extract ALL input fields (C1-C30, hidden fields, etc.) generically, calculate total units and gross amount, set computed hidden fields like `UNITSUM`, `AMTSUM`.
**Result:** `already_entered_days` variable name error — I renamed it to `days_already_entered` in parsing but referenced the old name later.

## Iteration 5 — Fix: variable name + rate extraction
**What:** Added `already_entered_days = list(days_already_entered)`. Fixed unit rate extraction to look for `var monthlyrate = 143.130` in JS.
**Result:** `success=True`, rate=143.13. But response still showed C11 empty — the form was POSTing to `/invoices/unitcalendar` instead of the correct update endpoint.
**Root cause:** The JavaScript `SubmitForm()` function sets `form.action="/invoices/unitcalendarupdate"`. The display URL is `/invoices/unitcalendar`, but the save URL is `/invoices/unitcalendarupdate`.

## Iteration 6 — Fix: correct form action URL + missing fields
**What:** Changed form action to `/invoices/unitcalendarupdate`. Added extraction of `linenumber` and `authorizationnum` from JS. Set `NETAMT` properly.
**Result:** **DATA SAVED!** Response shows C11="1.00", TOTALUNITS=4.00, RC Units Billed=4.0. `success=True, days_entered=1`.

## Iteration 7 — Fix: billing summary parsing
**What:** Fixed billing summary parsing to use `var totalunits` and `var monthlyrate` from response JS (since GROSSAMT and NETAMT are JS-computed, not in HTML). Cleaned up debug file saving.
**Result:** `success=True`, day 11 now shows as already_entered (confirming persistence from iteration 6). RC Units=4.0, RC Gross=572.52, RC Net=572.52, RC Unit Rate=143.13. `RESULT: PASS`.

---

## Summary of Fixes

1. **Calendar navigation:** Changed from GET to POST (`/invoices/unitcalendar` with `invoicedetid`, `updatemode`, `invoiceid`)
2. **Form action URL:** Changed from `/invoices/unitcalendar` to `/invoices/unitcalendarupdate`
3. **Form data:** Include ALL form fields (C1-C30, ABSENCETYPES, hidden fields), not just changed ones
4. **Computed fields:** Set `UNITSUM`, `AMTSUM`, `TOTALUNITS`, `GROSSAMT`, `NETAMT`, `linenumber`, `authorizationnum`
5. **Rate extraction:** Parse `var monthlyrate = X` from calendar JS
6. **Success logic:** Only count already-entered days that overlap with requested service_days
7. **Billing summary:** Parse from JS vars (`totalunits`, `monthlyrate`) since HTML fields are JS-populated

SUCCESS
