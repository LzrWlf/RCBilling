import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    UPLOAD_FOLDER = BASE_DIR / 'uploads'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload

    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or f'sqlite:///{BASE_DIR / "rcbilling.db"}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Encryption key for credentials (generate with: Fernet.generate_key())
    CREDENTIAL_KEY = os.environ.get('CREDENTIAL_KEY') or None

    # DDS eBilling portal URLs
    RC_PORTAL_URLS = {
        'SGPRC': 'https://ebilling.dds.ca.gov:8379/login',
        'ELARC': 'https://ebilling.dds.ca.gov:8373/login',
    }
