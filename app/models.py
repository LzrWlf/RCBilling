"""
Database models for RCBilling SaaS
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from cryptography.fernet import Fernet
import base64
import os

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """User/Clinic account"""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    clinic_name = db.Column(db.String(200), nullable=False)

    # Role: 'admin' or 'clinic'
    role = db.Column(db.String(20), default='clinic')

    # Subscription status
    is_active = db.Column(db.Boolean, default=True)
    subscription_start = db.Column(db.DateTime, default=datetime.utcnow)
    subscription_end = db.Column(db.DateTime, nullable=True)

    # Clinic settings
    regional_center = db.Column(db.String(20), default='ELARC')
    provider_name = db.Column(db.String(200), default='')

    # Encrypted eBilling credentials
    ebilling_username = db.Column(db.String(256), nullable=True)
    ebilling_password_encrypted = db.Column(db.Text, nullable=True)
    _encryption_key = db.Column(db.String(256), nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    # Usage tracking
    total_submissions = db.Column(db.Integer, default=0)
    total_services = db.Column(db.Integer, default=0)

    def set_password(self, password):
        """Hash and store password"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verify password"""
        return check_password_hash(self.password_hash, password)

    def set_ebilling_credentials(self, username, password):
        """Encrypt and store eBilling portal credentials"""
        # Generate encryption key if not exists
        if not self._encryption_key:
            self._encryption_key = base64.urlsafe_b64encode(os.urandom(32)).decode()

        key = self._encryption_key.encode()
        # Pad key to 32 bytes for Fernet
        key = base64.urlsafe_b64encode(base64.urlsafe_b64decode(key)[:32])
        fernet = Fernet(key)

        self.ebilling_username = username
        self.ebilling_password_encrypted = fernet.encrypt(password.encode()).decode()

    def get_ebilling_credentials(self):
        """Decrypt and return eBilling credentials"""
        if not self.ebilling_username or not self.ebilling_password_encrypted:
            return None, None

        if not self._encryption_key:
            return self.ebilling_username, None

        try:
            key = self._encryption_key.encode()
            key = base64.urlsafe_b64encode(base64.urlsafe_b64decode(key)[:32])
            fernet = Fernet(key)
            password = fernet.decrypt(self.ebilling_password_encrypted.encode()).decode()
            return self.ebilling_username, password
        except:
            return self.ebilling_username, None

    @property
    def is_subscription_active(self):
        """Check if subscription is currently active"""
        if not self.is_active:
            return False
        if self.subscription_end and datetime.utcnow() > self.subscription_end:
            return False
        return True

    @property
    def is_admin(self):
        """Check if user is admin"""
        return self.role == 'admin'

    def __repr__(self):
        return f'<User {self.email} ({self.clinic_name})>'


class SubmissionLog(db.Model):
    """Log of all submissions for billing/audit"""
    __tablename__ = 'submission_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    # Submission details
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    filename = db.Column(db.String(255), nullable=True)
    total_records = db.Column(db.Integer, default=0)
    successful = db.Column(db.Integer, default=0)
    failed = db.Column(db.Integer, default=0)
    total_services = db.Column(db.Integer, default=0)

    # Relationship
    user = db.relationship('User', backref=db.backref('submissions', lazy=True))

    def __repr__(self):
        return f'<SubmissionLog {self.id} - {self.user.clinic_name} - {self.timestamp}>'
