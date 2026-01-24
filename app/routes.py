from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, Response, session
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import os
import csv
import io
from datetime import datetime
from app.csv_parser import parse_rc_billing_csv, records_to_dict
from app.automation.dds_ebilling import submit_to_ebilling
from app.models import db, Provider, SubmissionLog

_last_submission_results = {}

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

        success_count = sum(1 for r in results if r.success)
        failed_count = len(results) - success_count

        result_details = []
        for i, r in enumerate(results):
            orig = records[i] if i < len(records) else {}
            result_details.append({
                'consumer_name': r.consumer_name or orig.get('consumer_name', ''),
                'uci': r.uci or orig.get('uci', ''),
                'auth_number': orig.get('auth_number', ''),
                'svc_code': orig.get('svc_code', ''),
                'svc_subcode': orig.get('svc_subcode', ''),
                'service_month': orig.get('service_month', ''),
                'service_days': orig.get('service_days', []),
                'expected_days': len(orig.get('service_days', [])),
                'success': r.success,
                'days_entered': r.days_entered,
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
            'failed_count': failed_count,
            'results': result_details
        }

        # Log submission
        total_services = sum(r.get('expected_days', 0) for r in result_details)
        log = SubmissionLog(
            user_id=current_user.id,
            provider_id=provider.id,
            filename=claims_data.get('filename', ''),
            total_records=len(results),
            successful=success_count,
            failed=failed_count,
            total_services=total_services
        )
        db.session.add(log)

        provider.total_submissions += 1
        provider.total_services += total_services
        db.session.commit()

        return jsonify({
            'status': 'complete',
            'message': f'Submitted {success_count} of {len(results)} records',
            'success_count': success_count,
            'failed_count': failed_count,
            'results': result_details,
            'has_errors': failed_count > 0
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
        'Service Month', 'Service Days', 'Days Entered',
        'Invoice Units', 'Invoice Amount',  # From CSV (may be empty)
        'RC Units', 'RC Gross', 'RC Net', 'RC Unit Rate',  # From RC Portal
        'Error'
    ])

    for r in sorted(user_results['results'], key=lambda x: (x['success'], x['consumer_name'])):
        # Format billing values - show empty string if zero/not available
        inv_units = r.get('invoice_units', 0)
        inv_amount = r.get('invoice_amount', 0)
        rc_units = r.get('rc_units', 0)
        rc_gross = r.get('rc_gross', 0)
        rc_net = r.get('rc_net', 0)
        rc_rate = r.get('rc_unit_rate', 0)

        writer.writerow([
            'SUCCESS' if r['success'] else 'FAILED',
            r['consumer_name'], r['uci'], r['auth_number'], r['svc_code'], r['svc_subcode'],
            r['service_month'], ', '.join(str(d) for d in r.get('service_days', [])),
            r['days_entered'],
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
    writer.writerow(['Total', user_results['total_records']])
    writer.writerow(['Success', user_results['success_count']])
    writer.writerow(['Failed', user_results['failed_count']])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=submission_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'}
    )
