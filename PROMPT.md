# Fix Invoice Retrieval: Expand Multi-Consumer Folders + Scroll Virtual Grids

## Your Task

Run the invoice scraper test and verify that INVOICE_COUNT is significantly higher than 31. Two bugs have been fixed:

1. **Folder expansion bug (FIXED):** `expand_multi_consumer_folder()` was matching by `(svc_code, svc_month)` instead of `invoice_id`, causing it to click the same row repeatedly. Now uses `invoice_id` for precise matching.
2. **Scroll wait time (FIXED):** Increased from 0.3s to 1.0s for Dojo DataGrid virtual scrolling.

**After each iteration:**
1. Run: `source venv/bin/activate && python test_invoice_scrape.py`
2. Append what you tried and the result to `LOOP_LOG.md`
3. If INVOICE_COUNT is significantly higher than 31, write "SUCCESS" as the very last line of `LOOP_LOG.md`

## Known Invoice Counts (PP0212 — WONDERKIND PEDIATRIC)

Use these to validate that folder expansion is working:
- Invoice 2607469 (10/2025): **6 consumers** inside
- Invoice 2610388 (12/2025): **30 consumers** inside
- Invoice 2621658 (12/2025): **9 consumers** inside
- PP0212 alone should yield **45+ expanded invoices**

## Background (Round 1 — FIXED, do NOT touch)

Provider iteration is working. The bot scans all 4 providers (HP0197, PP0212, PP0508, PP1829). **Do NOT modify provider iteration, navigation, login, or the basic cell-parsing logic.**

## What Was Fixed (Round 2)

### Fix 1: `open_invoice_details()` — Added invoice_id matching (Method 0)
New parameter `invoice_id: str = None`. When provided, matches by `cells[1].innerText == invoice_id` to click the exact row. Falls through to existing methods if no match.

### Fix 2: `expand_multi_consumer_folder()` — Now passes invoice_id
Changed from `open_invoice_details(None, svc_month, svc_code)` to include `invoice_id=invoice_id`.

### Fix 3: `_scrape_invoices_from_dojo_grid()` — Scroll wait increased to 1.0s
Gives Dojo DataGrid more time to render virtual rows after scrolling.

## If Something Goes Wrong

### Folder expansion not clicking the right row
- Take a screenshot before and after the EDIT click
- Check that `cells[1]` really contains the invoice ID (it might be cells[0] if checkbox isn't in a `<td>`)
- Try matching by text content: `row.innerText.includes(invoice_id)`

### Scroll loop not finding more rows
- The invoice search page may use a standard HTML table (not Dojo DataGrid) — check if `.dojoxGridView` elements exist
- If it's a standard HTML table, all rows are already rendered — no scrolling needed
- The Dojo fallback only runs when standard HTML scraping finds 0 invoices

## Rules
- Make ONE targeted fix per iteration — don't rewrite entire methods
- Take screenshots with descriptive names for debugging
- Do NOT modify credentials, .env, or test_invoice_scrape.py
- Do NOT change login(), navigate_to_provider_selection(), or provider iteration

## Test Command
```bash
source venv/bin/activate && python test_invoice_scrape.py
```

Success = INVOICE_COUNT significantly higher than 31 (ideally 50+) and `RESULT: PASS` in the output.
