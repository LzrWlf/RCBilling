from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, Response
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import os
import csv
import io
from pathlib import Path
from datetime import datetime
from app.csv_parser import parse_rc_billing_csv, records_to_dict
from app.automation.dds_ebilling import submit_to_ebilling
from app.models import db, SubmissionLog

# Store last submission results for report download (per-user in production, use session/cache)
_last_submission_results = {}

main_bp = Blueprint('main', __name__)

ALLOWED_EXTENSIONS = {'csv'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@main_bp.route('/')
@login_required
def index():
    # Check subscription
    if not current_user.is_subscription_active and not current_user.is_admin:
        flash('Your subscription has expired. Please contact support.', 'error')
        return redirect(url_for('auth.clinic_settings'))
    return render_template('upload.html')


@main_bp.route('/upload', methods=['POST'])
@login_required
def upload_file():
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

        # Parse the CSV and convert to dicts for template
        try:
            records_obj = parse_rc_billing_csv(filepath)
            records = records_to_dict(records_obj)
            return render_template('preview.html', claims=records, filename=filename)
        except Exception as e:
            flash(f'Error parsing CSV: {str(e)}', 'error')
            return redirect(url_for('main.index'))

    flash('Invalid file type. Please upload a CSV file.', 'error')
    return redirect(url_for('main.index'))


@main_bp.route('/submit', methods=['POST'])
@login_required
def submit_claims():
    """Submit claims to DDS eBilling portal"""
    global _last_submission_results

    # Check subscription
    if not current_user.is_subscription_active and not current_user.is_admin:
        return jsonify({
            'status': 'error',
            'message': 'Subscription expired. Please contact support.'
        })

    claims_data = request.json
    records = claims_data.get('claims', [])

    if not records:
        return jsonify({
            'status': 'error',
            'message': 'No records to submit'
        })

    # Get credentials from current user
    username, password = current_user.get_ebilling_credentials()

    if not username or not password:
        return jsonify({
            'status': 'error',
            'message': 'eBilling credentials not configured. Go to Settings.'
        })

    # Use clinic's settings
    regional_center = current_user.regional_center or 'ELARC'
    provider_name = current_user.provider_name or ''

    if not provider_name:
        return jsonify({
            'status': 'error',
            'message': 'Provider name not configured. Go to Settings.'
        })

    # Run automation
    try:
        results = submit_to_ebilling(
            records=records,
            username=username,
            password=password,
            provider_name=provider_name,
            regional_center=regional_center
        )

        # Summarize results
        success_count = sum(1 for r in results if r.success)
        failed_count = len(results) - success_count

        # Build detailed results including original record data
        result_details = []
        for i, r in enumerate(results):
            # Get original record data if available
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
                'error': r.error_message or ''
            })

        # Store for report download (keyed by user id)
        _last_submission_results[current_user.id] = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_records': len(results),
            'success_count': success_count,
            'failed_count': failed_count,
            'results': result_details
        }

        # Log submission to database
        total_services = sum(r.get('expected_days', 0) for r in result_details)
        log = SubmissionLog(
            user_id=current_user.id,
            filename=claims_data.get('filename', ''),
            total_records=len(results),
            successful=success_count,
            failed=failed_count,
            total_services=total_services
        )
        db.session.add(log)

        # Update user stats
        current_user.total_submissions += 1
        current_user.total_services += total_services
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
        return jsonify({
            'status': 'error',
            'message': f'Automation failed: {str(e)}'
        })


@main_bp.route('/download-report')
@login_required
def download_report():
    """Download submission report as CSV"""
    global _last_submission_results

    user_results = _last_submission_results.get(current_user.id)
    if not user_results:
        return "No submission results available", 404

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow([
        'Status',
        'Consumer Name',
        'UCI',
        'Auth Number',
        'SVC Code',
        'SVC Subcode',
        'Service Month',
        'Service Days',
        'Expected Days',
        'Days Entered',
        'Error Message'
    ])

    # Write data rows - errors first, then successes
    results = user_results['results']
    # Sort: failures first
    sorted_results = sorted(results, key=lambda x: (x['success'], x['consumer_name']))

    for r in sorted_results:
        status = 'SUCCESS' if r['success'] else 'FAILED'
        service_days_str = ', '.join(str(d) for d in r.get('service_days', []))

        writer.writerow([
            status,
            r['consumer_name'],
            r['uci'],
            r['auth_number'],
            r['svc_code'],
            r['svc_subcode'],
            r['service_month'],
            service_days_str,
            r['expected_days'],
            r['days_entered'],
            r['error']
        ])

    # Add summary at the end
    writer.writerow([])
    writer.writerow(['SUMMARY'])
    writer.writerow(['Submission Time', user_results['timestamp']])
    writer.writerow(['Total Records', user_results['total_records']])
    writer.writerow(['Successful', user_results['success_count']])
    writer.writerow(['Failed', user_results['failed_count']])

    # Prepare response
    output.seek(0)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'submission_report_{timestamp}.csv'

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


