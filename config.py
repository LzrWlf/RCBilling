import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

class Config:
    # Security: Require SECRET_KEY in production (no insecure fallback)
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        if os.environ.get('FLASK_ENV') == 'development':
            SECRET_KEY = 'dev-secret-key-for-local-only'
        else:
            raise ValueError("SECRET_KEY environment variable is required")

    UPLOAD_FOLDER = BASE_DIR / 'uploads'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload

    # Database - Railway provides DATABASE_URL automatically
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or f'sqlite:///{BASE_DIR / "rcbilling.db"}'
    # Fix for Railway PostgreSQL (postgres:// -> postgresql://)
    if SQLALCHEMY_DATABASE_URI.startswith('postgres://'):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Encryption key for credentials (generate with: Fernet.generate_key())
    CREDENTIAL_KEY = os.environ.get('CREDENTIAL_KEY') or None

    # Server port (Railway sets PORT automatically)
    PORT = int(os.environ.get('PORT', 5000))

    # Playwright headless mode (default True for production)
    PLAYWRIGHT_HEADLESS = os.environ.get('PLAYWRIGHT_HEADLESS', 'true').lower() == 'true'

    # Session cookie security (HTTPS-only in production)
    SESSION_COOKIE_SECURE = os.environ.get('FLASK_ENV') != 'development'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # DDS eBilling portal URLs
    RC_PORTAL_URLS = {
        'SGPRC': 'https://ebilling.dds.ca.gov:8379/login',
        'ELARC': 'https://ebilling.dds.ca.gov:8373/login',
    }
