# Fix Fast Billing Submission: Calendar Form via HTTP

## Your Task

Debug and fix `submit_to_ebilling_fast()` in `app/automation/dds_ebilling.py` until it successfully submits a billing record via direct HTTP requests (no Playwright for navigation).

**Test record:** Kim Austin, UCI 2719815, SPN PP0212, SVC 116/1CGEV, Sep 2025, day 11, 1 unit, $429.39.

**After each iteration:**
1. Run the test command below
2. Append what you tried and the result to `LOOP_LOG.md`
3. If the submission succeeds (days_entered > 0, success=True), write "SUCCESS" as the very last line of `LOOP_LOG.md`

## Test Command
```bash
source venv/bin/activate && python test_fast_submit.py
```

## Test Script

`test_fast_submit.py` already exists. It reads credentials from DB, calls `submit_to_ebilling_fast()` with Kim Austin's record, and prints results. Do NOT recreate it.

## Known Portal Data (from network capture)

The portal has this invoice for Kim Austin:
- Invoice Number: 2607468
- Invoice Internal ID: 1249519 (the `ID` field from `invoicegrid`)
- Service Code: 116, Service Month: 09/2025
- Consumer line in `invoiceviewgrid`: ID=3672501, BOCLID=2719815, BOSVCD=116, BOSVSC=1CGEV
- Kim Austin already has 1 day attended and 3.00 units billed

## Key Endpoints (from network log)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/home/dashboardspngrid` | GET | Get providers list (JSON) |
| `/home/setspn` | POST | Select provider |
| `/invoices/invoice` | GET | Load invoices page |
| `/invoices/invoicegrid` | GET | Get invoice list (JSON) |
| `/invoices/invoiceviewgrid/invoiceid/{id}/mode/A` | GET | Get consumer details (JSON) |
| `/invoices/invoiceview` | POST | Open invoice for editing (data: `invoiceid=<id>&updatemode=Y&selectallrecords=&invoiceno=&TARGET=`) |
| `/invoices/unitcalendar` | GET/POST | Calendar page — **this is the unknown** |

## Debugging Strategy

1. **First iteration:** Run the test. The likely failure is navigating to the calendar page.
   - Save the HTML response from `/invoices/unitcalendar` to `debug_calendar.html`
   - Also save the HTML from `/invoices/invoiceview` POST to `debug_invoiceview.html`
   - Inspect the HTML to find how to navigate to the calendar for a specific consumer line

2. **Calendar navigation:** The invoice view page has a table of consumers. Each row has a "Days Attend" link. Find that link's URL pattern by parsing the HTML.

3. **Calendar form:** Once you get the calendar HTML, find:
   - The `<form>` element and its action URL
   - Hidden input field names and values
   - Day input field names (they'll be in `<td>` cells with day numbers)
   - Which days are disabled vs available

4. **Calendar POST:** Build the form data and POST it. Check the response for success.

## Important Notes

- The portal is at `https://ebilling.dds.ca.gov:8379` with SSL verification disabled
- Session cookie is `PHPSESSID` set on domain `ebilling.dds.ca.gov`
- Kim Austin already has data entered — day 11 might already have a value. If so, the bot should detect "already_entered" and that's still a valid test result.
- Save HTML responses to files for debugging — they're critical for understanding the form structure
- Make ONE targeted fix per iteration
- Do NOT modify login logic, credentials, or .env files

## Rules
- Make targeted fixes — don't rewrite entire functions
- Save debug HTML to files for inspection
- Log everything to LOOP_LOG.md
- Only write "SUCCESS" when days_entered > 0 or already_entered_days is non-empty
