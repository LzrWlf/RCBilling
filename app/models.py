"""
Database models for RCBilling SaaS
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from cryptography.fernet import Fernet
import base64
import os

db = SQLAlchemy()

# All 21 California Regional Centers with their eBilling portal URLs
# Verified from official RC websites January 2026
REGIONAL_CENTERS = {
    'ACRC': ('Alta California Regional Center', 'https://ebilling.dds.ca.gov:8364/login'),
    'CVRC': ('Central Valley Regional Center', 'https://ebilling.dds.ca.gov:8367/login'),
    'ELARC': ('Eastern Los Angeles Regional Center', 'https://ebilling.dds.ca.gov:8373/login'),
    'FNRC': ('Far Northern Regional Center', 'https://ebilling.dds.ca.gov:8363/login'),
    'FDLRC': ('Frank D. Lanterman Regional Center', 'https://ebilling.dds.ca.gov:8360/login'),
    'GGRC': ('Golden Gate Regional Center', 'https://ebilling.dds.ca.gov:8361/login'),
    'HRC': ('Harbor Regional Center', 'https://ebilling.dds.ca.gov:8375/login'),
    'IRC': ('Inland Regional Center', 'https://ebilling.inlandrc.org/login'),
    'KRC': ('Kern Regional Center', 'https://ebilling.dds.ca.gov:8372/login'),
    'NBRC': ('North Bay Regional Center', 'https://ebilling.dds.ca.gov:8371/login'),
    'NLACRC': ('North Los Angeles County Regional Center', 'https://ebilling.dds.ca.gov:8378/login'),
    'RCRC': ('Redwood Coast Regional Center', 'https://ebilling.dds.ca.gov:8370/login'),
    'RCEB': ('Regional Center of the East Bay', 'https://ebilling.dds.ca.gov:8380/login'),
    'RCOC': ('Regional Center of Orange County', 'https://ebilling.dds.ca.gov:8368/login'),
    'SARC': ('San Andreas Regional Center', 'https://ebilling.dds.ca.gov:8365/login'),
    'SDRC': ('San Diego Regional Center', 'https://ebilling.dds.ca.gov:8362/login'),
    'SGPRC': ('San Gabriel/Pomona Regional Center', 'https://ebilling.dds.ca.gov:8379/login'),
    'SCLARC': ('South Central Los Angeles Regional Center', 'https://ebilling.dds.ca.gov:8374/login'),
    'TCRC': ('Tri-Counties Regional Center', 'https://ebilling.dds.ca.gov:8366/login'),
    'VMRC': ('Valley Mountain Regional Center', 'https://ebilling.dds.ca.gov:8377/login'),
    'WRC': ('Westside Regional Center', 'https://ebilling.dds.ca.gov:8369/login'),
}


class User(UserMixin, db.Model):
    """User account"""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(200), nullable=False)

    role = db.Column(db.String(20), default='user')
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    providers = db.relationship('Provider', backref='user', lazy='dynamic',
                                cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == 'admin'

    def __repr__(self):
        return f'<User {self.email}>'


class Provider(db.Model):
    """Provider with eBilling credentials for a Regional Center"""
    __tablename__ = 'providers'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    # Provider info
    name = db.Column(db.String(200), nullable=False)
    regional_center = db.Column(db.String(20), nullable=False)

    # eBilling credentials
    username = db.Column(db.String(256), nullable=True)
    password_encrypted = db.Column(db.Text, nullable=True)
    _encryption_key = db.Column(db.String(256), nullable=True)

    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    total_submissions = db.Column(db.Integer, default=0)
    total_services = db.Column(db.Integer, default=0)

    def set_credentials(self, username, password):
        if not self._encryption_key:
            self._encryption_key = base64.urlsafe_b64encode(os.urandom(32)).decode()

        key = self._encryption_key.encode()
        key = base64.urlsafe_b64encode(base64.urlsafe_b64decode(key)[:32])
        fernet = Fernet(key)

        self.username = username
        self.password_encrypted = fernet.encrypt(password.encode()).decode()
        self.updated_at = datetime.utcnow()

    def get_credentials(self):
        if not self.username or not self.password_encrypted:
            return None, None

        if not self._encryption_key:
            return self.username, None

        try:
            key = self._encryption_key.encode()
            key = base64.urlsafe_b64encode(base64.urlsafe_b64decode(key)[:32])
            fernet = Fernet(key)
            password = fernet.decrypt(self.password_encrypted.encode()).decode()
            return self.username, password
        except:
            return self.username, None

    @property
    def rc_name(self):
        rc = REGIONAL_CENTERS.get(self.regional_center)
        return rc[0] if rc else self.regional_center

    @property
    def rc_portal_url(self):
        rc = REGIONAL_CENTERS.get(self.regional_center)
        return rc[1] if rc else None

    def __repr__(self):
        return f'<Provider {self.name} ({self.regional_center})>'


class SubmissionLog(db.Model):
    """Log of submissions"""
    __tablename__ = 'submission_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    provider_id = db.Column(db.Integer, db.ForeignKey('providers.id'), nullable=True)

    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    filename = db.Column(db.String(255), nullable=True)
    total_records = db.Column(db.Integer, default=0)
    successful = db.Column(db.Integer, default=0)
    failed = db.Column(db.Integer, default=0)
    total_services = db.Column(db.Integer, default=0)

    user = db.relationship('User', backref=db.backref('submissions', lazy='dynamic'))
    provider = db.relationship('Provider', backref=db.backref('submissions', lazy='dynamic'))

    def __repr__(self):
        return f'<SubmissionLog {self.id}>'
