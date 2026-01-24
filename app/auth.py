"""
Authentication and settings routes
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app.models import db, User, Provider, REGIONAL_CENTERS
from datetime import datetime

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        username = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(email=username.lower()).first()
        if not user:
            user = User.query.filter(User.email.ilike(username)).first()

        if user and user.check_password(password):
            if not user.is_active:
                flash('Account deactivated.', 'error')
                return render_template('auth/login.html')

            login_user(user, remember=True)
            user.last_login = datetime.utcnow()
            db.session.commit()

            return redirect(request.args.get('next') or url_for('main.index'))
        else:
            flash('Invalid username or password', 'error')

    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/settings')
@login_required
def settings():
    return render_template('auth/settings.html',
                           providers=current_user.providers.all(),
                           regional_centers=REGIONAL_CENTERS)


@auth_bp.route('/provider/add', methods=['POST'])
@login_required
def add_provider():
    """Add a new provider"""
    name = request.form.get('name', '').strip()
    regional_center = request.form.get('regional_center', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()

    if not name:
        flash('Provider name is required', 'error')
        return redirect(url_for('auth.settings'))

    if not regional_center or regional_center not in REGIONAL_CENTERS:
        flash('Please select a Regional Center', 'error')
        return redirect(url_for('auth.settings'))

    provider = Provider(
        user_id=current_user.id,
        name=name,
        regional_center=regional_center
    )

    if username and password:
        provider.set_credentials(username, password)

    db.session.add(provider)
    db.session.commit()

    flash(f'Provider "{name}" added for {regional_center}', 'success')
    return redirect(url_for('auth.settings'))


@auth_bp.route('/provider/<int:provider_id>/update', methods=['POST'])
@login_required
def update_provider(provider_id):
    """Update provider credentials"""
    provider = Provider.query.get_or_404(provider_id)
    if provider.user_id != current_user.id:
        flash('Access denied', 'error')
        return redirect(url_for('auth.settings'))

    name = request.form.get('name', '').strip()
    regional_center = request.form.get('regional_center', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()

    if name:
        provider.name = name
    if regional_center and regional_center in REGIONAL_CENTERS:
        provider.regional_center = regional_center

    if username and password:
        provider.set_credentials(username, password)

    db.session.commit()
    flash(f'Provider updated', 'success')
    return redirect(url_for('auth.settings'))


@auth_bp.route('/provider/<int:provider_id>/delete', methods=['POST'])
@login_required
def delete_provider(provider_id):
    """Delete a provider"""
    provider = Provider.query.get_or_404(provider_id)
    if provider.user_id != current_user.id:
        flash('Access denied', 'error')
        return redirect(url_for('auth.settings'))

    name = provider.name
    db.session.delete(provider)
    db.session.commit()
    flash(f'Provider "{name}" removed', 'success')
    return redirect(url_for('auth.settings'))
