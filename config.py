import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    UPLOAD_FOLDER = BASE_DIR / 'uploads'
    DATABASE_PATH = BASE_DIR / 'rcbilling.db'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload

    # Encryption key for credentials (generate with: Fernet.generate_key())
    CREDENTIAL_KEY = os.environ.get('CREDENTIAL_KEY') or None

    # DDS eBilling portal (SGPRC = port 8379, ELARC = port 8373)
    EBILLING_URL = 'https://ebilling.dds.ca.gov:8379/login'
