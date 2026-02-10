from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, Response, session
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import os
import csv
import io
from datetime import datetime
from app.csv_parser import parse_rc_billing_csv, records_to_dict
from app.automation.dds_ebilling import submit_to_ebilling, scrape_invoice_inventory, scrape_all_providers_inventory
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
        results = submit_to_ebilling(
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

        _last_submission_results[current_user.id] = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'provider_name': provider.name,
            'total_records': len(results),
            'success_count': success_count,
            'partial_count': partial_count,
            'skipped_count': skipped_count,
            'failed_count': failed_count,
            'results': result_details
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

    # Header row with invoice vs RC portal comparison columns
    writer.writerow([
        'Status', 'Consumer Name', 'UCI', 'Auth Number', 'SVC Code', 'SVC Subcode',
        'Service Month', 'Service Days', 'Days Entered', 'Days Expected', 'Unavailable Days', 'Already Entered',
        'Invoice Units', 'Invoice Amount',  # From CSV (may be empty)
        'RC Units', 'RC Gross', 'RC Net', 'RC Unit Rate',  # From RC Portal
        'Error'
    ])

    # Sort: success first, then partial, then skipped, then failed
    def sort_key(x):
        if x['success'] and not x.get('partial'):
            return (0, x['consumer_name'])
        elif x.get('partial'):
            return (1, x['consumer_name'])
        elif x.get('skipped'):
            return (2, x['consumer_name'])
        else:
            return (3, x['consumer_name'])

    for r in sorted(user_results['results'], key=sort_key):
        # Format billing values - show empty string if zero/not available
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

    writer.writerow([])
    writer.writerow(['SUMMARY'])
    writer.writerow(['Time', user_results['timestamp']])
    writer.writerow(['Provider', user_results.get('provider_name', '')])
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
        result = scrape_all_providers_inventory(
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
        writer.writerow(['Provider SPN', 'Last Name', 'First Name', 'UCI', 'Service Month', 'Service Code', 'Invoice ID'])
    else:
        writer.writerow(['Last Name', 'First Name', 'UCI', 'Service Month', 'Service Code', 'Invoice ID'])

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
            inv.get('invoice_id', '')
        ])
        writer.writerow(row)

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=available_invoices_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'}
    )
