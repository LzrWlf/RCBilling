from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, Response, session
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import os
import csv
import io
from datetime import datetime
from app.csv_parser import parse_rc_billing_csv, records_to_dict
from app.automation.dds_ebilling import submit_to_ebilling, submit_to_ebilling_fast, scrape_invoice_inventory, scrape_all_providers_inventory, scrape_all_providers_inventory_fast, submit_fm_invoice_fast, FMUploadResult
from app.models import db, Provider, SubmissionLog

_last_submission_results = {}
_last_available_invoices = {}

main_bp = Blueprint('main', __name__)

ALLOWED_EXTENSIONS = {'csv'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@main_bp.route('/')
@login_required
def index():
    providers = current_user.providers.order_by(Provider.name).all()
    return render_template('upload.html', providers=providers)


@main_bp.route('/upload', methods=['POST'])
@login_required
def upload_file():
    provider_id = request.form.get('provider_id')

    if not provider_id:
        flash('Please select a Regional Center', 'error')
        return redirect(url_for('main.index'))

    provider = Provider.query.get(provider_id)
    if not provider or provider.user_id != current_user.id:
        flash('Invalid Regional Center selection', 'error')
        return redirect(url_for('main.index'))

    session['selected_provider_id'] = int(provider_id)

    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('main.index'))

    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('main.index'))

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        try:
            records_obj = parse_rc_billing_csv(filepath)
            records = records_to_dict(records_obj)
            return render_template('preview.html',
                                   claims=records,
                                   filename=filename,
                                   provider=provider)
        except Exception as e:
            flash(f'Error parsing CSV: {str(e)}', 'error')
            return redirect(url_for('main.index'))

    flash('Invalid file type. Please upload a CSV file.', 'error')
    return redirect(url_for('main.index'))


@main_bp.route('/submit', methods=['POST'])
@login_required
def submit_claims():
    global _last_submission_results

    claims_data = request.json
    records = claims_data.get('claims', [])
    provider_id = claims_data.get('provider_id') or session.get('selected_provider_id')

    if not records:
        return jsonify({'status': 'error', 'message': 'No records to submit'})

    provider = Provider.query.get(provider_id) if provider_id else None
    if not provider or provider.user_id != current_user.id:
        return jsonify({'status': 'error', 'message': 'Regional Center not selected'})

    username, password = provider.get_credentials()
    if not username or not password:
        return jsonify({'status': 'error', 'message': f'No credentials for {provider.regional_center}. Go to Settings.'})

    try:
        # Use spn_id from CSV records for provider selection (not provider.name)
        # This allows matching by SPN ID in the portal's provider table
        results, portal_invoice_totals = submit_to_ebilling_fast(
            records=records,
            username=username,
            password=password,
            provider_name=None,  # Let it use spn_id from records
            regional_center=provider.regional_center,
            portal_url=provider.rc_portal_url
        )

        # Count by status category
        success_count = sum(1 for r in results if r.success and not r.partial)
        partial_count = sum(1 for r in results if r.partial)
        skipped_count = sum(1 for r in results if not r.success and not r.partial and r.error_message and r.error_message.startswith('SKIPPED:'))
        failed_count = len(results) - success_count - partial_count - skipped_count

        # Build lookup for original records by UCI
        orig_by_uci = {rec.get('uci', ''): rec for rec in records}

        result_details = []
        for r in results:
            # Find matching original record by UCI
            orig = orig_by_uci.get(r.uci, {})
            is_skipped = r.error_message and r.error_message.startswith('SKIPPED:')
            result_details.append({
                'consumer_name': r.consumer_name or orig.get('consumer_name', ''),
                'uci': r.uci or orig.get('uci', ''),
                'invoice_id': r.invoice_id or '',
                'auth_number': orig.get('auth_number', ''),
                'svc_code': orig.get('svc_code', ''),
                'svc_subcode': orig.get('svc_subcode', ''),
                'service_month': orig.get('service_month', ''),
                'service_days': orig.get('service_days', []),
                'expected_days': r.days_expected or len(orig.get('service_days', [])),
                'success': r.success,
                'partial': r.partial,
                'skipped': is_skipped,
                'days_entered': r.days_entered,
                'unavailable_days': r.unavailable_days or [],
                'already_entered_days': r.already_entered_days or [],
                'error': r.error_message or '',
                # Invoice data from CSV (may be empty)
                'invoice_units': r.invoice_units,
                'invoice_amount': r.invoice_amount,
                # RC Portal data (captured after update)
                'rc_units': r.rc_units_billed,
                'rc_gross': r.rc_gross_amount,
                'rc_net': r.rc_net_amount,
                'rc_unit_rate': r.rc_unit_rate
            })

        # Build invoice-level summary (sub-invoice stats per invoice)
        # portal_invoice_totals has the TOTAL consumer lines per invoice from the portal
        from collections import defaultdict as _defaultdict
        _inv_groups = _defaultdict(list)
        for r in result_details:
            inv_id = r.get('invoice_id', '') or 'NO_INVOICE'
            _inv_groups[inv_id].append(r)

        def _inv_sort_key(inv_id):
            try:
                return (0, int(inv_id))
            except (ValueError, TypeError):
                return (1, str(inv_id))

        # Track which invoice IDs we've already summarised
        _seen_inv_ids = set()

        invoice_summary = []
        for inv_id in sorted(_inv_groups.keys(), key=_inv_sort_key):
            records_in_inv = _inv_groups[inv_id]
            with_days = sum(1 for r in records_in_inv if (r.get('days_entered') or 0) > 0 or r.get('already_entered_days'))
            # Use portal total if available, otherwise fall back to submitted count
            portal_total = portal_invoice_totals.get(inv_id, len(records_in_inv)) if inv_id != 'NO_INVOICE' else len(records_in_inv)
            zero_days = portal_total - with_days
            invoice_summary.append({
                'invoice_id': inv_id if inv_id != 'NO_INVOICE' else '',
                'total_sub_invoices': portal_total,
                'sub_invoices_zero_days': zero_days
            })
            _seen_inv_ids.add(inv_id)

        # Add portal invoices that had NO submitted records
        for inv_id in sorted(portal_invoice_totals.keys(), key=_inv_sort_key):
            if inv_id not in _seen_inv_ids:
                portal_total = portal_invoice_totals[inv_id]
                invoice_summary.append({
                    'invoice_id': inv_id,
                    'total_sub_invoices': portal_total,
                    'sub_invoices_zero_days': portal_total  # all zero-day since nothing submitted
                })

        _last_submission_results[current_user.id] = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'provider_name': provider.name,
            'total_records': len(results),
            'success_count': success_count,
            'partial_count': partial_count,
            'skipped_count': skipped_count,
            'failed_count': failed_count,
            'results': result_details,
            'invoice_summary': invoice_summary
        }

        # Log submission (only count actual attempts, not skipped)
        total_services = sum(r.get('expected_days', 0) for r in result_details if not r.get('skipped'))
        log = SubmissionLog(
            user_id=current_user.id,
            provider_id=provider.id,
            filename=claims_data.get('filename', ''),
            total_records=len(results) - skipped_count,  # Actual attempts
            successful=success_count,
            failed=failed_count,
            total_services=total_services
        )
        db.session.add(log)

        provider.total_submissions += 1
        provider.total_services += total_services
        db.session.commit()

        # Build message with status breakdown
        processed = len(results) - skipped_count
        parts = [f'{success_count} success']
        if partial_count > 0:
            parts.append(f'{partial_count} partial')
        if failed_count > 0:
            parts.append(f'{failed_count} failed')
        if skipped_count > 0:
            parts.append(f'{skipped_count} skipped')
        message = f'Processed {processed} records: ' + ', '.join(parts)

        return jsonify({
            'status': 'complete',
            'message': message,
            'success_count': success_count,
            'partial_count': partial_count,
            'skipped_count': skipped_count,
            'failed_count': failed_count,
            'results': result_details,
            'invoice_summary': invoice_summary,
            'has_errors': failed_count > 0 or partial_count > 0
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Automation failed: {str(e)}'})


@main_bp.route('/download-report')
@login_required
def download_report():
    global _last_submission_results

    user_results = _last_submission_results.get(current_user.id)
    if not user_results:
        return "No submission results available", 404

    output = io.StringIO()
    writer = csv.writer(output)

    # Header row with invoice number column
    writer.writerow([
        'Invoice #', 'Status', 'Consumer Name', 'UCI', 'Auth Number', 'SVC Code', 'SVC Subcode',
        'Service Month', 'Service Days', 'Days Entered', 'Days Expected', 'Unavailable Days', 'Already Entered',
        'Invoice Units', 'Invoice Amount',  # From CSV (may be empty)
        'RC Units', 'RC Gross', 'RC Net', 'RC Unit Rate',  # From RC Portal
        'Error'
    ])

    # Group results by invoice number
    from collections import defaultdict
    invoices_grouped = defaultdict(list)
    for r in user_results['results']:
        invoice_id = r.get('invoice_id', '') or 'NO_INVOICE'
        invoices_grouped[invoice_id].append(r)

    # Sort invoice numbers (numeric sort if possible)
    def invoice_sort_key(inv_id):
        try:
            return (0, int(inv_id))
        except (ValueError, TypeError):
            return (1, str(inv_id))

    sorted_invoice_ids = sorted(invoices_grouped.keys(), key=invoice_sort_key)

    # Write rows grouped by invoice with summary rows
    for invoice_id in sorted_invoice_ids:
        records = invoices_grouped[invoice_id]

        # Sort records within invoice by consumer name
        records_sorted = sorted(records, key=lambda x: x.get('consumer_name', ''))

        # Count sub-invoice stats
        submitted_subs = len(records)
        subs_with_days = sum(1 for r in records if (r.get('days_entered') or 0) > 0 or r.get('already_entered_days'))
        # Find portal total from invoice_summary data
        inv_sum_entry = next((s for s in user_results.get('invoice_summary', [])
                              if s.get('invoice_id', '') == (invoice_id if invoice_id != 'NO_INVOICE' else '')), None)
        total_subs = inv_sum_entry['total_sub_invoices'] if inv_sum_entry else submitted_subs
        subs_zero_days = total_subs - subs_with_days

        # Write invoice summary row (spans across columns for visibility)
        display_inv = invoice_id if invoice_id != 'NO_INVOICE' else '(No Invoice #)'
        writer.writerow([
            f'--- INVOICE: {display_inv} ---',
            f'{total_subs} sub-invoices',
            f'{subs_zero_days} with 0 days attended',
            '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''
        ])

        # Write detail rows for this invoice
        for r in records_sorted:
            # Format billing values
            inv_units = r.get('invoice_units', 0)
            inv_amount = r.get('invoice_amount', 0)
            rc_units = r.get('rc_units', 0)
            rc_gross = r.get('rc_gross', 0)
            rc_net = r.get('rc_net', 0)
            rc_rate = r.get('rc_unit_rate', 0)

            # Determine status
            if r['success'] and not r.get('partial'):
                status = 'SUCCESS'
            elif r.get('partial'):
                status = 'PARTIAL'
            elif r.get('skipped'):
                status = 'SKIPPED'
            else:
                status = 'FAILED'

            # Format unavailable days and already entered days
            unavailable = r.get('unavailable_days', [])
            unavailable_str = ', '.join(str(d) for d in unavailable) if unavailable else ''
            already_entered = r.get('already_entered_days', [])
            already_entered_str = ', '.join(str(d) for d in already_entered) if already_entered else ''

            writer.writerow([
                invoice_id if invoice_id != 'NO_INVOICE' else '',
                status,
                r['consumer_name'], r['uci'], r['auth_number'], r['svc_code'], r['svc_subcode'],
                r['service_month'], ', '.join(str(d) for d in r.get('service_days', [])),
                r['days_entered'], r.get('expected_days', ''), unavailable_str, already_entered_str,
                f'{inv_units:.2f}' if inv_units else '',
                f'${inv_amount:.2f}' if inv_amount else '',
                f'{rc_units:.2f}' if rc_units else '',
                f'${rc_gross:.2f}' if rc_gross else '',
                f'${rc_net:.2f}' if rc_net else '',
                f'${rc_rate:.2f}' if rc_rate else '',
                r['error']
            ])

        # Blank row between invoices
        writer.writerow([])

    # Invoice-level summary table
    writer.writerow(['INVOICE SUMMARY'])
    writer.writerow(['Invoice #', 'Sub Invoices', '0 Days Attended'])
    for inv_sum in user_results.get('invoice_summary', []):
        writer.writerow([
            inv_sum.get('invoice_id') or '(No Invoice #)',
            inv_sum['total_sub_invoices'],
            inv_sum['sub_invoices_zero_days']
        ])
    writer.writerow([])

    writer.writerow(['OVERALL SUMMARY'])
    writer.writerow(['Time', user_results['timestamp']])
    writer.writerow(['Provider', user_results.get('provider_name', '')])
    writer.writerow(['Total Invoices', len(sorted_invoice_ids)])
    writer.writerow(['Total Records', user_results['total_records']])
    writer.writerow(['Success', user_results['success_count']])
    writer.writerow(['Partial (some days unavailable)', user_results.get('partial_count', 0)])
    writer.writerow(['Skipped (no matching invoice)', user_results.get('skipped_count', 0)])
    writer.writerow(['Failed', user_results['failed_count']])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=submission_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'}
    )


@main_bp.route('/available-invoices', methods=['POST'])
@login_required
def get_available_invoices():
    """Generate a report of all available invoices on the RC portal"""
    global _last_available_invoices

    provider_id = request.form.get('provider_id')
    if not provider_id:
        flash('Please select a provider', 'error')
        return redirect(url_for('main.index'))

    provider = Provider.query.get(provider_id)
    if not provider or provider.user_id != current_user.id:
        flash('Invalid provider selection', 'error')
        return redirect(url_for('main.index'))

    username, password = provider.get_credentials()
    if not username or not password:
        flash(f'No credentials for {provider.regional_center}. Go to Settings.', 'error')
        return redirect(url_for('main.index'))

    try:
        # Call inventory-only function with SPN ID for provider selection
        result = scrape_invoice_inventory(
            username=username,
            password=password,
            regional_center=provider.regional_center,
            portal_url=provider.rc_portal_url,
            provider_id=provider.spn_id
        )

        if result['status'] != 'success':
            flash(result['message'], 'error')
            return redirect(url_for('main.index'))

        for w in result.get('warnings', []):
            flash(w, 'warning')

        inventory = result['invoices']

        # Sort by last name, then first name
        inventory = sorted(inventory, key=lambda x: (
            (x.get('last_name') or '').upper(),
            (x.get('first_name') or '').upper()
        ))

        # Store for download
        _last_available_invoices[current_user.id] = {
            'timestamp': datetime.now(),
            'provider_name': provider.name,
            'invoices': inventory
        }

        return render_template('available_invoices.html',
                               invoices=inventory,
                               provider=provider)

    except Exception as e:
        flash(f'Error scraping invoices: {str(e)}', 'error')
        return redirect(url_for('main.index'))


@main_bp.route('/available-invoices-ajax', methods=['POST'])
@login_required
def get_available_invoices_ajax():
    """AJAX endpoint to get available invoices using SPN ID from uploaded CSV"""
    global _last_available_invoices

    data = request.json
    provider_id = data.get('provider_id')
    spn_id = data.get('spn_id')  # SPN ID from the uploaded CSV

    if not provider_id:
        return jsonify({'status': 'error', 'message': 'Provider not specified'})

    provider = Provider.query.get(provider_id)
    if not provider or provider.user_id != current_user.id:
        return jsonify({'status': 'error', 'message': 'Invalid provider'})

    username, password = provider.get_credentials()
    if not username or not password:
        return jsonify({'status': 'error', 'message': f'No credentials for {provider.regional_center}. Go to Settings.'})

    if not spn_id:
        return jsonify({'status': 'error', 'message': 'No SPN ID found in uploaded CSV'})

    try:
        # Use SPN ID from the CSV for provider selection
        result = scrape_invoice_inventory(
            username=username,
            password=password,
            regional_center=provider.regional_center,
            portal_url=provider.rc_portal_url,
            provider_id=spn_id  # Use SPN ID from CSV
        )

        if result['status'] != 'success':
            return jsonify({'status': 'error', 'message': result['message']})

        inventory = result['invoices']

        # Sort by last name, then first name
        inventory = sorted(inventory, key=lambda x: (
            (x.get('last_name') or '').upper(),
            (x.get('first_name') or '').upper()
        ))

        # Store for download
        _last_available_invoices[current_user.id] = {
            'timestamp': datetime.now(),
            'provider_name': provider.name,
            'invoices': inventory
        }

        return jsonify({
            'status': 'success',
            'invoices': inventory,
            'count': len(inventory),
            'warnings': result.get('warnings', [])
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error scraping invoices: {str(e)}'})


@main_bp.route('/available-invoices-all', methods=['POST'])
@login_required
def get_available_invoices_all():
    """Scan all providers on an RC login and return combined invoice inventory"""
    global _last_available_invoices

    provider_id = request.form.get('provider_id')
    if not provider_id:
        flash('Please select a provider', 'error')
        return redirect(url_for('main.index'))

    provider = Provider.query.get(provider_id)
    if not provider or provider.user_id != current_user.id:
        flash('Invalid provider selection', 'error')
        return redirect(url_for('main.index'))

    username, password = provider.get_credentials()
    if not username or not password:
        flash(f'No credentials for {provider.regional_center}. Go to Settings.', 'error')
        return redirect(url_for('main.index'))

    try:
        result = scrape_all_providers_inventory_fast(
            username=username,
            password=password,
            regional_center=provider.regional_center,
            portal_url=provider.rc_portal_url
        )

        if result['status'] != 'success':
            flash(result['message'], 'error')
            return redirect(url_for('main.index'))

        for w in result.get('warnings', []):
            flash(w, 'warning')

        inventory = result['invoices']
        providers_scanned = result.get('providers_scanned', [])

        # Sort by last name, then first name
        inventory = sorted(inventory, key=lambda x: (
            (x.get('last_name') or '').upper(),
            (x.get('first_name') or '').upper()
        ))

        # Store for download
        _last_available_invoices[current_user.id] = {
            'timestamp': datetime.now(),
            'provider_name': provider.name,
            'invoices': inventory
        }

        flash(f'Scanned {len(providers_scanned)} providers, found {len(inventory)} invoices', 'success')
        return render_template('available_invoices.html',
                               invoices=inventory,
                               provider=provider,
                               providers_scanned=providers_scanned,
                               scan_all=True)

    except Exception as e:
        flash(f'Error scraping invoices: {str(e)}', 'error')
        return redirect(url_for('main.index'))


@main_bp.route('/download-available-invoices')
@login_required
def download_available_invoices():
    """Download the available invoices as a CSV file"""
    global _last_available_invoices

    user_results = _last_available_invoices.get(current_user.id)
    if not user_results:
        return "No inventory results available", 404

    output = io.StringIO()
    writer = csv.writer(output)

    # Include Provider SPN column if any invoice has it (all-providers scan)
    has_provider_spn = any(inv.get('provider_spn') for inv in user_results['invoices'])

    if has_provider_spn:
        writer.writerow(['Provider SPN', 'Last Name', 'First Name', 'UCI', 'Service Month', 'Service Code', 'SVC Subcode', 'Auth #', 'Auth Units', 'Invoice ID'])
    else:
        writer.writerow(['Last Name', 'First Name', 'UCI', 'Service Month', 'Service Code', 'SVC Subcode', 'Auth #', 'Auth Units', 'Invoice ID'])

    for inv in user_results['invoices']:
        row = []
        if has_provider_spn:
            row.append(inv.get('provider_spn', ''))
        row.extend([
            inv.get('last_name', ''),
            inv.get('first_name', ''),
            inv.get('uci', ''),
            inv.get('service_month', ''),
            inv.get('svc_code', ''),
            inv.get('svc_subcode', ''),
            inv.get('auth_number', ''),
            inv.get('auth_units', ''),
            inv.get('invoice_id', '')
        ])
        writer.writerow(row)

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=available_invoices_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'}
    )


# Store last FM submission results for download
_last_fm_results = {}


@main_bp.route('/submit-fm-invoice', methods=['POST'])
@login_required
def submit_fm_invoice():
    """
    Submit Filemaker invoice with capture-zero-enter workflow.

    Expects JSON:
    {
        "claims": [...],  # FM invoice records
        "provider_id": int
    }

    Returns JSON with FMUploadResult details.
    """
    global _last_fm_results

    claims_data = request.json
    records = claims_data.get('claims', [])
    provider_id = claims_data.get('provider_id') or session.get('selected_provider_id')

    if not records:
        return jsonify({'status': 'error', 'message': 'No records to submit'})

    if not provider_id:
        return jsonify({'status': 'error', 'message': 'No provider selected'})

    provider = Provider.query.get(provider_id)
    if not provider or provider.user_id != current_user.id:
        return jsonify({'status': 'error', 'message': 'Invalid provider'})

    username, password = provider.get_credentials()
    if not username or not password:
        return jsonify({'status': 'error', 'message': f'No credentials for {provider.regional_center}. Go to Settings.'})

    try:
        results = submit_fm_invoice_fast(
            records=records,
            username=username,
            password=password,
            provider_name=None,
            regional_center=provider.regional_center,
            portal_url=provider.rc_portal_url
        )

        # Build result details for response
        result_details = []
        success_count = 0
        partial_count = 0
        failed_count = 0
        skipped_count = 0

        for r in results:
            status = 'success' if r.success else 'failed'
            if r.error_message and r.error_message.startswith('SKIPPED:'):
                status = 'skipped'
                skipped_count += 1
            elif r.success:
                success_count += 1
            else:
                failed_count += 1

            # Format original values for display
            original_str = ''
            if r.original_values:
                orig_days = [f"{d}:{v}" for d, v in sorted(r.original_values.items()) if v > 0]
                original_str = ', '.join(orig_days) if orig_days else 'none'

            result_details.append({
                'status': status,
                'last_name': r.last_name,
                'first_name': r.first_name,
                'uci': r.uci,
                'invoice_id': r.invoice_id,
                'service_month': r.service_month,
                'svc_code': r.svc_code,
                'svc_subcode': r.svc_subcode,
                'auth_number': r.auth_number,
                'fm_days': r.fm_service_days,
                'original_values': original_str,
                'original_total': r.original_total_units,
                'days_zeroed': len(r.days_zeroed) if r.days_zeroed else 0,
                'days_entered': len(r.days_entered) if r.days_entered else 0,
                'days_unavailable': r.days_unavailable,
                'final_total': r.final_total_units,
                'final_gross': r.final_gross_amount,
                'retry_count': r.retry_count,
                'retry_reason': r.retry_reason,
                'error': r.error_message
            })

        # Store for download
        _last_fm_results[current_user.id] = {
            'timestamp': datetime.now(),
            'provider_name': provider.name,
            'results': results
        }

        return jsonify({
            'status': 'complete',
            'message': f'Processed {len(results)} records: {success_count} success, {failed_count} failed, {skipped_count} skipped',
            'results': result_details,
            'summary': {
                'total': len(results),
                'success': success_count,
                'failed': failed_count,
                'skipped': skipped_count
            }
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@main_bp.route('/zero-out-fm-entries', methods=['POST'])
@login_required
def zero_out_fm_entries():
    """
    Zero out entries for FM invoice records without entering new values.
    This uses the same capture-zero workflow but skips entering FM values.
    """
    global _last_fm_results

    claims_data = request.json
    records = claims_data.get('claims', [])
    provider_id = claims_data.get('provider_id') or session.get('selected_provider_id')

    if not records:
        return jsonify({'status': 'error', 'message': 'No records to zero out'})

    if not provider_id:
        return jsonify({'status': 'error', 'message': 'No provider selected'})

    provider = Provider.query.get(provider_id)
    if not provider or provider.user_id != current_user.id:
        return jsonify({'status': 'error', 'message': 'Invalid provider'})

    username, password = provider.get_credentials()
    if not username or not password:
        return jsonify({'status': 'error', 'message': f'No credentials for {provider.regional_center}. Go to Settings.'})

    try:
        results = submit_fm_invoice_fast(
            records=records,
            username=username,
            password=password,
            provider_name=None,
            regional_center=provider.regional_center,
            portal_url=provider.rc_portal_url,
            zero_only=True  # Only zero out, don't enter new values
        )

        # Build result details for response
        result_details = []
        success_count = 0
        failed_count = 0
        skipped_count = 0

        for r in results:
            status = 'success' if r.success else 'failed'
            if r.error_message and r.error_message.startswith('SKIPPED:'):
                status = 'skipped'
                skipped_count += 1
            elif r.success:
                success_count += 1
            else:
                failed_count += 1

            # Format original values for display
            original_str = ''
            if r.original_values:
                orig_days = [f"{d}:{v}" for d, v in sorted(r.original_values.items()) if v > 0]
                original_str = ', '.join(orig_days) if orig_days else 'none'

            result_details.append({
                'status': status,
                'last_name': r.last_name,
                'first_name': r.first_name,
                'uci': r.uci,
                'invoice_id': r.invoice_id,
                'service_month': r.service_month,
                'svc_code': r.svc_code,
                'original_values': original_str,
                'original_total': r.original_total_units,
                'days_zeroed': len(r.days_zeroed) if r.days_zeroed else 0,
                'final_total': r.final_total_units,
                'error': r.error_message
            })

        # Store for download
        _last_fm_results[current_user.id] = {
            'timestamp': datetime.now(),
            'provider_name': provider.name,
            'results': results
        }

        return jsonify({
            'status': 'complete',
            'message': f'Zeroed out {len(results)} records: {success_count} success, {failed_count} failed, {skipped_count} skipped',
            'results': result_details,
            'summary': {
                'total': len(results),
                'success': success_count,
                'failed': failed_count,
                'skipped': skipped_count
            }
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@main_bp.route('/download-fm-report')
@login_required
def download_fm_report():
    """Download the FM invoice submission results as a CSV file"""
    global _last_fm_results

    user_results = _last_fm_results.get(current_user.id)
    if not user_results:
        flash('No FM submission results to download', 'error')
        return redirect(url_for('main.index'))

    results = user_results['results']
    provider_name = user_results['provider_name']

    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow([
        'Status',
        'Last Name',
        'First Name',
        'UCI',
        'Invoice ID',
        'Auth Number',
        'SVC Code',
        'SVC Subcode',
        'Service Month',
        'FM Days',
        'Original Values',
        'Original Total Units',
        'Days Zeroed',
        'Days Entered',
        'Days Unavailable',
        'Final Total Units',
        'Final Gross Amount',
        'Retry Count',
        'Retry Reason',
        'Error'
    ])

    # Write data rows
    for r in results:
        status = 'SUCCESS' if r.success else 'FAILED'
        if r.error_message and r.error_message.startswith('SKIPPED:'):
            status = 'SKIPPED'

        # Format original values
        original_str = ''
        if r.original_values:
            orig_days = [f"{d}:{v}" for d, v in sorted(r.original_values.items()) if v > 0]
            original_str = '; '.join(orig_days) if orig_days else ''

        # Format lists
        fm_days_str = ','.join(map(str, r.fm_service_days)) if r.fm_service_days else ''
        days_unavail_str = ','.join(map(str, r.days_unavailable)) if r.days_unavailable else ''

        writer.writerow([
            status,
            r.last_name,
            r.first_name,
            r.uci,
            r.invoice_id,
            r.auth_number,
            r.svc_code,
            r.svc_subcode,
            r.service_month,
            fm_days_str,
            original_str,
            r.original_total_units,
            len(r.days_zeroed) if r.days_zeroed else 0,
            len(r.days_entered) if r.days_entered else 0,
            days_unavail_str,
            r.final_total_units,
            r.final_gross_amount,
            r.retry_count,
            r.retry_reason or '',
            r.error_message or ''
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=fm_submission_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'}
    )
