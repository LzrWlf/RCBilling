"""
Admin dashboard for RCBilling SaaS
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.models import db, User, SubmissionLog
from datetime import datetime, timedelta
from functools import wraps

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    """Decorator to require admin role"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash('Admin access required.', 'error')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated_function


@admin_bp.route('/')
@admin_required
def dashboard():
    """Admin dashboard - overview of all clinics"""
    clinics = User.query.filter_by(role='clinic').order_by(User.created_at.desc()).all()

    # Get recent submissions
    recent_logs = SubmissionLog.query.order_by(SubmissionLog.timestamp.desc()).limit(20).all()

    # Stats
    total_clinics = len(clinics)
    active_clinics = sum(1 for c in clinics if c.is_subscription_active)
    total_submissions = db.session.query(db.func.sum(SubmissionLog.total_records)).scalar() or 0
    total_services = db.session.query(db.func.sum(SubmissionLog.total_services)).scalar() or 0

    return render_template('admin/dashboard.html',
                           clinics=clinics,
                           recent_logs=recent_logs,
                           total_clinics=total_clinics,
                           active_clinics=active_clinics,
                           total_submissions=total_submissions,
                           total_services=total_services)


@admin_bp.route('/clinic/add', methods=['GET', 'POST'])
@admin_required
def add_clinic():
    """Add a new clinic"""
    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')
        clinic_name = request.form.get('clinic_name', '')
        regional_center = request.form.get('regional_center', 'ELARC')
        provider_name = request.form.get('provider_name', '')

        # Validate
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return render_template('admin/add_clinic.html', regional_centers=['ELARC', 'SGPRC'])

        # Create clinic
        clinic = User(
            email=email,
            clinic_name=clinic_name,
            role='clinic',
            regional_center=regional_center,
            provider_name=provider_name,
            is_active=True,
            subscription_start=datetime.utcnow(),
            subscription_end=datetime.utcnow() + timedelta(days=30)  # 30-day initial
        )
        clinic.set_password(password)

        db.session.add(clinic)
        db.session.commit()

        flash(f'Clinic "{clinic_name}" created successfully!', 'success')
        return redirect(url_for('admin.dashboard'))

    return render_template('admin/add_clinic.html', regional_centers=['ELARC', 'SGPRC'])


@admin_bp.route('/clinic/<int:clinic_id>')
@admin_required
def view_clinic(clinic_id):
    """View clinic details"""
    clinic = User.query.get_or_404(clinic_id)
    submissions = SubmissionLog.query.filter_by(user_id=clinic_id).order_by(SubmissionLog.timestamp.desc()).limit(50).all()

    return render_template('admin/view_clinic.html', clinic=clinic, submissions=submissions)


@admin_bp.route('/clinic/<int:clinic_id>/toggle', methods=['POST'])
@admin_required
def toggle_clinic(clinic_id):
    """Enable/disable clinic subscription"""
    clinic = User.query.get_or_404(clinic_id)
    clinic.is_active = not clinic.is_active
    db.session.commit()

    status = 'activated' if clinic.is_active else 'deactivated'
    flash(f'Clinic "{clinic.clinic_name}" has been {status}.', 'success')
    return redirect(url_for('admin.view_clinic', clinic_id=clinic_id))


@admin_bp.route('/clinic/<int:clinic_id>/extend', methods=['POST'])
@admin_required
def extend_subscription(clinic_id):
    """Extend clinic subscription"""
    clinic = User.query.get_or_404(clinic_id)
    days = int(request.form.get('days', 30))

    if clinic.subscription_end and clinic.subscription_end > datetime.utcnow():
        # Extend from current end date
        clinic.subscription_end = clinic.subscription_end + timedelta(days=days)
    else:
        # Start fresh from today
        clinic.subscription_end = datetime.utcnow() + timedelta(days=days)

    clinic.is_active = True
    db.session.commit()

    flash(f'Subscription extended by {days} days.', 'success')
    return redirect(url_for('admin.view_clinic', clinic_id=clinic_id))


@admin_bp.route('/clinic/<int:clinic_id>/delete', methods=['POST'])
@admin_required
def delete_clinic(clinic_id):
    """Delete a clinic (soft delete - just deactivate)"""
    clinic = User.query.get_or_404(clinic_id)

    # Delete submission logs first
    SubmissionLog.query.filter_by(user_id=clinic_id).delete()

    # Delete clinic
    db.session.delete(clinic)
    db.session.commit()

    flash(f'Clinic "{clinic.clinic_name}" has been deleted.', 'success')
    return redirect(url_for('admin.dashboard'))
