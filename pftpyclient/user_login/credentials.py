import sqlite3
import json
import shutil
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.fernet import Fernet
from pathlib import Path
from loguru import logger
from xrpl.core import addresscodec
from xrpl.core.keypairs.ed25519 import ED25519
import base64
from pftpyclient.postfiatsecurity.hash_tools import derive_shared_secret
import time
import re
from xrpl.wallet import Wallet
CREDENTIALS_DB = "credentials.sqlite"
BACKUP_SUFFIX = ".sqlite_backup"

KEY_EXPIRY = -1  # expiry in seconds, set to -1 for no expiration

def get_credentials_directory():
    """Returns the path to the credentials directory, creating it if it doesn't exist"""
    creds_dir = Path.home().joinpath("postfiatcreds")
    creds_dir.mkdir(exist_ok=True)
    return creds_dir

def get_database_path():
    return get_credentials_directory() / CREDENTIALS_DB

class CredentialManager:
    def __init__(self, username, password, allow_new_user=False):
        """Initialize CredentialManager
        
        Args:
            username: Username to manage credentials for
            password: Password for encryption/decryption
            allow_new_user: If True, skip password verification for new users
        """
        self.postfiat_username = username.lower()
        self.db_path = get_database_path()
        if not allow_new_user and not self.verify_password(password):
            raise ValueError("Invalid username or password")
        self.encryption_key = self._derive_encryption_key(password)
        self._key_expiry = time.time() + KEY_EXPIRY if KEY_EXPIRY >= 0 else float('inf')
        self._initialize_database()
        self.ecdh_public_key = None 

    def verify_password(self, password) -> bool:
        """Verify password by attempting to decrypt a known credential"""
        test_key = self._derive_encryption_key(password)
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT encrypted_value FROM credentials 
                    WHERE username = ? and key like '%xrpaddress'
                    LIMIT 1;
                """, (self.postfiat_username,))
                row = cursor.fetchone()
                if row:
                    fernet = Fernet(test_key)
                    fernet.decrypt(row[0].encode())
                    return True
        except Exception as e:
            logger.error(f"Failed to verify password: {e}")
            return False
        
    def get_credential(self, credential_type):
        """Get a specific credential by type"""
        self._check_key_expiry()

        key = f"{self.postfiat_username}__{credential_type}"
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT encrypted_value FROM credentials 
                WHERE username = ? and key = ?;
            """, (self.postfiat_username, key))
            row = cursor.fetchone()
            if row:
                return self._decrypt_value(row[0])
        return None
    
    def _check_key_expiry(self):
        """Check if encryption key has expired"""
        if KEY_EXPIRY >= 0 and time.time() > self._key_expiry:
            self.clear_credentials()
            raise CredentialsExpiredError("Encryption key has expired. Please re-authenticate.")

    @classmethod
    def cache_credentials(cls, input_map) -> bool:
        """
        Cache user credentials locally in the SQLite database.

        Args:
            input_map: Dictionary containing user credentials
        Returns:
            string message indicating the result of the operation, or raises an exception

        Validates:
            - Username format (lowercase, alphanumeric, underscores)
            - Password length (minimum 8 characters)
            - XRP address validity
            - XRP secret validity and correspondence to address
        """
        try:
            # Extract and validate username
            username = input_map['Username_Input'].lower()
            if not cls.is_valid_username(username):
                error_msg = "Username must contain only letters, numbers, and underscores"
                logger.error(f"CredentialManager: {error_msg}")
                raise ValueError(error_msg)

            # Check if username already exists
            if username in cls.get_cached_usernames():
                error_msg = f"Username {username} already exists"
                logger.error(f"CredentialManager: {error_msg}")
                raise ValueError(error_msg)
            
            # Validate password
            password = input_map['Password_Input']
            if not cls.is_valid_password(password):
                error_msg = "Password must be at least 8 characters long and contain only letters, numbers, or basic symbols"
                logger.error(f"CredentialManager: {error_msg}")
                raise ValueError(error_msg)
            
            # Validate XRP address and secret
            xrp_address = input_map['XRP Address_Input']
            xrp_secret = input_map['XRP Secret_Input']

            try:
                # Validate XRP address format
                if not addresscodec.is_valid_classic_address(xrp_address):
                    error_msg = "Invalid XRP address"
                    logger.error(f"CredentialManager: {error_msg}")
                    raise ValueError(error_msg)
                
                # Validate secret and check if it corresponds to address
                try:
                    wallet = Wallet.from_seed(xrp_secret)
                    if wallet.classic_address != xrp_address:
                        error_msg = "XRP secret does not correspond to address"
                        logger.error(f"CredentialManager: {error_msg}")
                        raise ValueError(error_msg)
                except Exception as e:
                    error_msg = f"Invalid XRP secret: {e}"
                    logger.error(f"CredentialManager: {error_msg}")
                    raise ValueError(error_msg)

            except Exception as e:
                error_msg = f"Invalid XRP credentials"
                logger.error(f"CredentialManager: {error_msg}")
                raise ValueError(error_msg)

            # If all validations pass, cache credentials
            credentials = {
                f"{username}__v1xrpaddress": xrp_address,
                f"{username}__v1xrpsecret": xrp_secret,
            }

            # Create a CredentialManager instance for the new user
            manager = cls(username=username, password=password, allow_new_user=True)

            # Encrypt and store the credentials
            manager.enter_and_encrypt_credential(credentials_dict=credentials)

            return f"User credentials encrypted using password and cached to {get_credentials_directory() / CREDENTIALS_DB}"
        
        except Exception as e:
            logger.error(f"CredentialManager: Error caching credentials: {e}")
            raise
        
    @staticmethod
    def is_valid_username(username):
        """Check if username contains only letters, numbers, and underscores"""
        return bool(re.match(r'^[a-z0-9_]+$', username))
    
    @staticmethod
    def is_valid_password(password):
        """Check if password is at least 8 characters long and contains only letters, numbers, or basic symbols"""
        if len(password) < 8:
            return False

        allowed_chars = set(
            'abcdefghijklmnopqrstuvwxyz'
            'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
            '0123456789'
            '!@#$%^&*()_+-=[]{}|;:,.<>?'
        )
        
        return all(char in allowed_chars for char in password)

    @classmethod
    def get_cached_usernames(cls):
        """Returns a list of unique usernames from cached credentials in the database"""
        try:
            db_path = get_database_path()
            if not db_path.exists():
                logger.warning(f"Database does not exist at {db_path}")
                return []
            
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                # Query distinct usernames from the credentials table
                cursor.execute("SELECT DISTINCT username FROM credentials;")
                usernames = [row[0] for row in cursor.fetchall()]

            return sorted(usernames)
        
        except Exception as e:
            logger.error(f"Error getting cached usernames: {e}")
            return []

    @staticmethod
    def _derive_encryption_key(password):
        """Derive an encryption key from the password"""
        kdf = PBKDF2HMAC(
            algorithm=SHA256(),
            length=32,
            salt=b'postfiat_salt',
            iterations=100000,
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))
    
    def _initialize_database(self):
        """Initialize the SQLite database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Create credentials table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS credentials (
                    username TEXT NOT NULL,
                    key TEXT NOT NULL,
                    encrypted_value TEXT NOT NULL,
                    PRIMARY KEY (username, key)
                );
            """)
            # Create contacts table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    username TEXT NOT NULL,
                    address TEXT NOT NULL,
                    name TEXT NOT NULL,  -- encrypted
                    PRIMARY KEY (username, address)
                );
            """)
            conn.commit()
        logger.debug(f"Initialized database at {self.db_path}")

    def _encrypt_value(self, value):
        """Encrypt a value using the derived encryption key"""
        fernet = Fernet(self.encryption_key)
        return fernet.encrypt(value.encode()).decode()
    
    def _decrypt_value(self, encrypted_value):
        """Decrypt a value using the derived encryption key"""
        fernet = Fernet(self.encryption_key)
        return fernet.decrypt(encrypted_value.encode()).decode()
    
    def _decrypt_creds(self):
        """Retrieve and decrypt all credentials for the user"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT key, encrypted_value FROM credentials WHERE username = ?;
            """, (self.postfiat_username,))
            rows = cursor.fetchall()
        return {key: self._decrypt_value(value) for key, value in rows}
    
    def enter_and_encrypt_credential(self, credentials_dict):
        """Encrypt and store multiple credentials"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for key, value in credentials_dict.items():
                encrypted_value = self._encrypt_value(value)
                cursor.execute("""
                    INSERT OR REPLACE INTO credentials (username, key, encrypted_value)
                    VALUES (?, ?, ?);
                """, (self.postfiat_username, key, encrypted_value))
            conn.commit()
            logger.info(f"Stored {len(credentials_dict)} credentials for {self.postfiat_username}")

    def change_password(self, new_password) -> bool:
        """Change the encryption password for the current user's credentials"""
        try:
            if not self.is_valid_password(new_password):
                raise ValueError("New password must be at least 8 characters long and contain only letters, numbers, or basic symbols")

            creds = self._decrypt_creds()
            contacts = self.get_contacts()
            self._backup_database()
            self.encryption_key = self._derive_encryption_key(new_password)

            # Re-encrypt and store credentials 
            self.enter_and_encrypt_credential(creds)

            # Re-encrypt and store contacts
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                # First clear existing contacts
                cursor.execute("""
                    DELETE FROM contacts WHERE username = ?;
                """, (self.postfiat_username,))

                # Re-insert contacts with newly encrypted names
                for address, name in contacts.items():
                    encrypted_name = self._encrypt_value(name)
                    cursor.execute("""
                        INSERT OR REPLACE INTO contacts (username, address, name)
                        VALUES (?, ?, ?);
                    """, (self.postfiat_username, address, encrypted_name))
                conn.commit()

            logger.info(f"Password changed for {self.postfiat_username}")
            return True
        except Exception as e:
            logger.error(f"Failed to change password: {e}")
            return False

    def clear_credentials(self):
        """Clear all credentials from memory."""
        # Overwrite encryption key with zeros and then set to None
        if self.encryption_key:
            self.encryption_key = '0' * len(self.encryption_key)
            self.encryption_key = None
        # Overwrite ECDH public key with zeros and then set to None
        if self.ecdh_public_key:
            self.ecdh_public_key = '0' * len(self.ecdh_public_key)
            self.ecdh_public_key = None

    def delete_credentials(self):
        """Delete all credentials for the current user"""
        self._backup_database()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Delete all credentials
            cursor.execute("""
                DELETE FROM credentials WHERE username = ?;
            """, (self.postfiat_username,))
            # Delete all contacts
            cursor.execute("""
                DELETE FROM contacts WHERE username = ?;
            """, (self.postfiat_username,))
            conn.commit()
        logger.info(f"Deleted all credentials and contactsfor {self.postfiat_username}")

    def get_contacts(self):
        """Retrieve all contacts for the user"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT address, name FROM contacts WHERE username = ?;
            """, (self.postfiat_username,))
            contacts = cursor.fetchall()
            return {
                address: self._decrypt_value(name)
                for address, name in contacts
            }

    def save_contact(self, address, name):
        """Save or update a contact"""
        # Check if contact already exists
        existing_contacts = self.get_contacts()
        if address in existing_contacts:
            error_msg = f"Contact with address {address} already exists"
            logger.error(f"CredentialManager: {error_msg}")
            raise ValueError(error_msg)

        encrypted_name = self._encrypt_value(name)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO contacts (username, address, name)
                VALUES (?, ?, ?);
            """, (self.postfiat_username, address, encrypted_name))
            conn.commit()
            logger.info(f"Saved contact {name} at {address} for {self.postfiat_username}")

    def delete_contact(self, address):
        """Delete a contact"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM contacts WHERE username = ? AND address = ?;
            """, (self.postfiat_username, address))
            conn.commit()
            logger.info(f"Deleted contact at {address} for {self.postfiat_username}")

    def _backup_database(self):
        """Create a backup of the current database"""
        backup_path = self.db_path.with_suffix(BACKUP_SUFFIX)
        shutil.copy2(self.db_path, backup_path)
        logger.info(f"Created backup of database at {backup_path}")

    def _get_raw_entropy(self):
        """Returns the raw entropy bytes from the wallet secret"""
        wallet_secret = self.get_credential('v1xrpsecret')
        decoded_seed = addresscodec.decode_seed(wallet_secret)
        return decoded_seed[0]
    
    def _derive_ecdh_public_key(self):
        """Derives ECDH public key from wallet secret"""
        raw_entropy = self._get_raw_entropy()
        self.ecdh_public_key, _ = ED25519.derive_keypair(raw_entropy, is_validator=False)
    
    def get_ecdh_public_key(self):
        """Returns ECDH public key as hex string"""
        if self.ecdh_public_key is None:
            self._derive_ecdh_public_key()
        return self.ecdh_public_key

    def get_shared_secret(self, received_key):
        """Derive a shared secret using ECDH"""
        raw_entropy = self._get_raw_entropy()
        return derive_shared_secret(public_key_hex=received_key, seed_bytes=raw_entropy)

class CredentialsExpiredError(Exception):
    """Exception raised when the encryption key has expired"""
    pass
