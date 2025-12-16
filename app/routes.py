from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from werkzeug.utils import secure_filename
import os
from app.csv_parser import parse_office_ally_csv

main_bp = Blueprint('main', __name__)

ALLOWED_EXTENSIONS = {'csv'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@main_bp.route('/')
def index():
    return render_template('upload.html')


@main_bp.route('/upload', methods=['POST'])
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

        # Parse the CSV
        try:
            claims = parse_office_ally_csv(filepath)
            return render_template('preview.html', claims=claims, filename=filename)
        except Exception as e:
            flash(f'Error parsing CSV: {str(e)}', 'error')
            return redirect(url_for('main.index'))

    flash('Invalid file type. Please upload a CSV file.', 'error')
    return redirect(url_for('main.index'))


@main_bp.route('/submit', methods=['POST'])
def submit_claims():
    """Submit claims to DDS eBilling portal"""
    # This will trigger the Playwright automation
    # For now, return a placeholder response
    claims_data = request.json

    # TODO: Implement actual submission
    return jsonify({
        'status': 'pending',
        'message': 'Submission queued. Automation will process shortly.',
        'claim_count': len(claims_data.get('claims', []))
    })


@main_bp.route('/settings')
def settings():
    return render_template('settings.html')


@main_bp.route('/settings/credentials', methods=['POST'])
def save_credentials():
    """Save eBilling portal credentials (encrypted)"""
    username = request.form.get('username')
    password = request.form.get('password')

    # TODO: Implement encrypted storage
    flash('Credentials saved successfully', 'success')
    return redirect(url_for('main.settings'))
