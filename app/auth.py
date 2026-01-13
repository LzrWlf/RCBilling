"""
Authentication routes for RCBilling SaaS
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app.models import db, User
from datetime import datetime

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            if not user.is_active:
                flash('Your account has been deactivated. Please contact support.', 'error')
                return render_template('auth/login.html')

            login_user(user, remember=True)
            user.last_login = datetime.utcnow()
            db.session.commit()

            next_page = request.args.get('next')
            if user.is_admin:
                return redirect(next_page or url_for('admin.dashboard'))
            return redirect(next_page or url_for('main.index'))
        else:
            flash('Invalid email or password', 'error')

    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    """Logout user"""
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def clinic_settings():
    """Clinic settings page - regional center, provider, credentials"""
    if request.method == 'POST':
        # Update clinic settings
        current_user.regional_center = request.form.get('regional_center', 'ELARC')
        current_user.provider_name = request.form.get('provider_name', '')

        # Update eBilling credentials if provided
        ebilling_username = request.form.get('ebilling_username', '')
        ebilling_password = request.form.get('ebilling_password', '')

        if ebilling_username and ebilling_password:
            current_user.set_ebilling_credentials(ebilling_username, ebilling_password)
        elif ebilling_username:
            # Just update username, keep existing password
            current_user.ebilling_username = ebilling_username

        db.session.commit()
        flash('Settings saved successfully!', 'success')
        return redirect(url_for('auth.clinic_settings'))

    # Get current credentials (username only, password hidden)
    eb_username, _ = current_user.get_ebilling_credentials()

    return render_template('auth/settings.html',
                           regional_centers=['ELARC', 'SGPRC'],
                           eb_username=eb_username or '')
