# Verify Sub-Invoice Tracking Feature

## Your Task

Verify that the sub-invoice tracking feature was implemented correctly across:
1. The submission JSON response (`invoice_summary` field)
2. The CSV download report (invoice summary rows + INVOICE SUMMARY section)
3. The website display (Invoice Summary card on available_invoices.html, and submission results in preview.html)

## What Was Changed

### routes.py
- `submit_claims()` now builds an `invoice_summary` list and includes it in both `_last_submission_results` and the JSON response
- Each invoice summary entry has: `invoice_id`, `total_sub_invoices`, `sub_invoices_with_days`, `sub_invoices_zero_days`
- `download_report()` now shows enhanced invoice summary rows with "X sub-invoices, Y with days attended, Z with 0 days attended"
- `download_report()` now has an INVOICE SUMMARY section before the OVERALL SUMMARY in the CSV

### preview.html (Submission Results)
- After submission, an "Invoice Summary" card is shown with a table listing each invoice and its sub-invoice counts
- The inline invoice header rows in the detail table now show badges: total sub-invoices, with days, with 0 days
- Warning styling if any invoice has sub-invoices with 0 days attended

### available_invoices.html (Invoice Search Results)
- New "Invoice Summary" card at top that groups sub-invoices by invoice_id
- Shows total sub-invoices, with days attended (auth_units > 0), and 0 days attended per invoice
- Toggle button to hide/show the summary
- Warning highlighting for invoices with 0-day sub-invoices

## Test Command
```bash
source venv/bin/activate && FLASK_ENV=development PORT=5001 python -c "
from app import create_app
app = create_app()
with app.app_context():
    # Verify routes.py compiles and functions exist
    from app.routes import main_bp, download_report, submit_claims
    print('routes.py: OK - submit_claims and download_report found')

    # Verify templates parse without errors
    from jinja2 import Environment
    import os
    template_dir = os.path.join(os.path.dirname(os.path.abspath('app')), 'app', 'templates')

    # Check preview.html has invoice_summary references
    with open('app/templates/preview.html') as f:
        preview = f.read()
    assert 'invoice_summary' in preview, 'preview.html missing invoice_summary'
    assert 'Invoice Summary' in preview, 'preview.html missing Invoice Summary card'
    assert 'sub_invoices_zero_days' in preview, 'preview.html missing zero days tracking'
    print('preview.html: OK - invoice_summary, Invoice Summary card, zero days tracking found')

    # Check available_invoices.html has summary card
    with open('app/templates/available_invoices.html') as f:
        avail = f.read()
    assert 'invoiceSummaryCard' in avail, 'available_invoices.html missing summary card'
    assert 'summaryTableBody' in avail, 'available_invoices.html missing summary table'
    assert 'With 0 Days Attended' in avail, 'available_invoices.html missing 0 days column'
    print('available_invoices.html: OK - summary card, summary table, 0 days column found')

    # Check routes.py has invoice_summary in response
    import inspect
    src = inspect.getsource(submit_claims)
    assert 'invoice_summary' in src, 'submit_claims missing invoice_summary'
    assert 'sub_invoices_with_days' in src, 'submit_claims missing sub_invoices_with_days'
    assert 'sub_invoices_zero_days' in src, 'submit_claims missing sub_invoices_zero_days'
    print('submit_claims(): OK - invoice_summary with sub-invoice stats found')

    src2 = inspect.getsource(download_report)
    assert 'INVOICE SUMMARY' in src2, 'download_report missing INVOICE SUMMARY section'
    assert 'sub_invoices_with_days' in src2, 'download_report missing sub_invoices_with_days'
    print('download_report(): OK - INVOICE SUMMARY section found')

    print()
    print('ALL CHECKS PASSED')
"
```

## After each iteration:
1. Run the test command above
2. Append what you checked and the result to `LOOP_LOG.md`
3. If ALL CHECKS PASSED appears, write "SUCCESS" as the very last line of `LOOP_LOG.md`
4. If any check fails, fix the issue and re-run

## Rules
- Make targeted fixes only if tests fail
- Log everything to LOOP_LOG.md
- Only write "SUCCESS" when ALL CHECKS PASSED
