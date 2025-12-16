"""
Secure Credential Manager using Fernet encryption

Stores eBilling portal credentials encrypted at rest.
"""
import os
import json
from pathlib import Path
from cryptography.fernet import Fernet
from typing import Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class CredentialManager:
    """
    Manages encrypted storage of portal credentials.

    Uses Fernet symmetric encryption to store credentials
    securely on disk.
    """

    def __init__(self, storage_path: Path, encryption_key: Optional[bytes] = None):
        """
        Initialize credential manager.

        Args:
            storage_path: Path to store encrypted credentials file
            encryption_key: Fernet key for encryption (generates if not provided)
        """
        self.storage_path = Path(storage_path)
        self.key_path = self.storage_path.parent / '.credential_key'

        if encryption_key:
            self.key = encryption_key
        else:
            self.key = self._load_or_generate_key()

        self.fernet = Fernet(self.key)

    def _load_or_generate_key(self) -> bytes:
        """Load existing key or generate new one"""
        if self.key_path.exists():
            return self.key_path.read_bytes()
        else:
            key = Fernet.generate_key()
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            self.key_path.write_bytes(key)
            # Restrict permissions on key file
            os.chmod(self.key_path, 0o600)
            logger.info(f"Generated new encryption key at {self.key_path}")
            return key

    def save_credentials(self, username: str, password: str, portal: str = "dds_ebilling") -> bool:
        """
        Save encrypted credentials.

        Args:
            username: Portal username
            password: Portal password
            portal: Portal identifier (for multi-portal support)

        Returns:
            True if saved successfully
        """
        try:
            # Load existing credentials or start fresh
            if self.storage_path.exists():
                encrypted_data = self.storage_path.read_bytes()
                decrypted = self.fernet.decrypt(encrypted_data)
                credentials = json.loads(decrypted)
            else:
                credentials = {}

            # Update credentials for this portal
            credentials[portal] = {
                'username': username,
                'password': password
            }

            # Encrypt and save
            encrypted = self.fernet.encrypt(json.dumps(credentials).encode())
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_path.write_bytes(encrypted)
            os.chmod(self.storage_path, 0o600)

            logger.info(f"Credentials saved for portal: {portal}")
            return True

        except Exception as e:
            logger.error(f"Failed to save credentials: {str(e)}")
            return False

    def get_credentials(self, portal: str = "dds_ebilling") -> Optional[Tuple[str, str]]:
        """
        Retrieve decrypted credentials.

        Args:
            portal: Portal identifier

        Returns:
            Tuple of (username, password) or None if not found
        """
        try:
            if not self.storage_path.exists():
                logger.warning("No credentials file found")
                return None

            encrypted_data = self.storage_path.read_bytes()
            decrypted = self.fernet.decrypt(encrypted_data)
            credentials = json.loads(decrypted)

            if portal not in credentials:
                logger.warning(f"No credentials found for portal: {portal}")
                return None

            cred = credentials[portal]
            return (cred['username'], cred['password'])

        except Exception as e:
            logger.error(f"Failed to retrieve credentials: {str(e)}")
            return None

    def delete_credentials(self, portal: str = "dds_ebilling") -> bool:
        """
        Delete credentials for a specific portal.

        Args:
            portal: Portal identifier

        Returns:
            True if deleted successfully
        """
        try:
            if not self.storage_path.exists():
                return True

            encrypted_data = self.storage_path.read_bytes()
            decrypted = self.fernet.decrypt(encrypted_data)
            credentials = json.loads(decrypted)

            if portal in credentials:
                del credentials[portal]

                if credentials:
                    # Re-encrypt remaining credentials
                    encrypted = self.fernet.encrypt(json.dumps(credentials).encode())
                    self.storage_path.write_bytes(encrypted)
                else:
                    # No credentials left, delete file
                    self.storage_path.unlink()

            logger.info(f"Credentials deleted for portal: {portal}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete credentials: {str(e)}")
            return False

    def has_credentials(self, portal: str = "dds_ebilling") -> bool:
        """Check if credentials exist for a portal"""
        return self.get_credentials(portal) is not None


# Default instance for the app
def get_credential_manager(app_config) -> CredentialManager:
    """Get credential manager instance using app config"""
    storage_path = Path(app_config.get('DATABASE_PATH', '.')).parent / '.credentials'
    key = app_config.get('CREDENTIAL_KEY')
    return CredentialManager(storage_path, key.encode() if key else None)
