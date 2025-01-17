from typing import Optional, Union, Dict, List
import base64
import hashlib
from cryptography.fernet import Fernet
from loguru import logger
from xrpl.wallet import Wallet
from pftpyclient.utilities.ecdh import ECDHUtils
from pftpyclient.sql.sql_manager import SQLManager

class MessageEncryption:
    """Handles encryption/decryption of messages using ECDH-derived shared secrets"""

    def __init__(self, sql_manager: SQLManager):
        """Initialize MessageEncryption with SQLManager for persistence"""
        self.sql_manager = sql_manager

    @staticmethod
    def encrypt_message(message: Union[str, bytes], shared_secret: Union[str, bytes]) -> str:
        """
        Encrypt a memo using a shared secret.
        
        Args:
            message: Message content to encrypt (string or bytes)
            shared_secret: The shared secret derived from ECDH
            
        Returns:
            str: Encrypted message content
        """
        # Convert shared_secret to bytes if it isn't already
        if isinstance(shared_secret, str):
            shared_secret = shared_secret.encode()

        # Generate Fernet key from shared secret
        key = base64.urlsafe_b64encode(hashlib.sha256(shared_secret).digest())
        fernet = Fernet(key)

        # Handle message input type
        if isinstance(message, str):
            message = message.encode()
        elif isinstance(message, bytes):
            pass
        else:
            raise ValueError(f"Message must be string or bytes, not {type(message)}")
        
        # Encrypt and return as string
        encrypted_bytes = fernet.encrypt(message)
        return encrypted_bytes.decode()

    @staticmethod
    def decrypt_message(encrypted_content: str, shared_secret: Union[str, bytes]) -> str:
        """
        Decrypt a message using a shared secret.
        
        Args:
            encrypted_content: The encrypted message content
            shared_secret: The shared secret derived from ECDH
            
        Returns:
            Decrypted message
        """
        # Ensure shared_secret is bytes
        if isinstance(shared_secret, str):
            shared_secret = shared_secret.encode()

        # Generate a Fernet key from the shared secret
        key = base64.urlsafe_b64encode(hashlib.sha256(shared_secret).digest())
        fernet = Fernet(key)

        # Decrypt the message
        decrypted_bytes = fernet.decrypt(encrypted_content.encode())
        return decrypted_bytes.decode()

    @staticmethod
    def get_ecdh_public_key_from_seed(wallet_seed: str) -> str:
        """Get ECDH public key directly from a wallet seed"""
        return ECDHUtils.get_ecdh_public_key_from_seed(wallet_seed)
    
    @staticmethod
    def get_shared_secret(received_public_key: str, channel_private_key: str) -> bytes:
        """Derive a shared secret using ECDH"""
        return ECDHUtils.get_shared_secret(received_public_key, channel_private_key)

    def get_handshake_for_address(
            self, 
            channel_address: str, 
            channel_counterparty: str
        ) -> tuple[Optional[str], Optional[str]]:
        """Get handshake public keys between two addresses from transaction history.
        
        Args:
            channel_address: One end of the encryption channel
            channel_counterparty: The other end of the encryption channel
            
        Returns:
            Tuple of (channel_address's ECDH public key, channel_counterparty's ECDH public key)
        """
        try:            
            # Validate addresses
            if not (channel_address.startswith('r') and channel_counterparty.startswith('r')):
                logger.error(f"Invalid XRPL addresses provided: {channel_address}, {channel_counterparty}")
                raise ValueError("Invalid XRPL addresses provided")

            # Query handshakes from database
            with self.sql_manager.get_connection() as conn:
                cursor = conn.execute(
                    self.sql_manager.load_query('xrpl', 'get_address_handshakes'),
                    (
                        channel_address, 
                        channel_address, channel_counterparty,
                        channel_counterparty, channel_address
                    )
                )
                handshakes = [dict(row) for row in cursor.fetchall()]

            if not handshakes:
                return None, None
            
            # Process handshakes
            sent_key = None
            received_key = None

            for handshake in handshakes:
                if handshake['direction'] == 'OUTGOING' and sent_key is None:
                    sent_key = handshake['memo_data']
                elif handshake['direction'] == 'INCOMING' and received_key is None:
                    received_key = handshake['memo_data']
                
                # Break early if we have both keys
                if sent_key and received_key:
                    break

            return sent_key, received_key
        
        except Exception as e:
            logger.error(f"Error checking handshake status: {e}")
            return None, None

    def encrypt_memo(self, memo: str, shared_secret: str) -> str:
        """Encrypts a memo using a shared secret"""
        return self.encrypt_message(memo, shared_secret)

    def process_encrypted_message(self, message: str, shared_secret: bytes) -> str:
        """Process an encrypted message"""
        try:
            decrypted_message = self.decrypt_message(message, shared_secret)
            return f"[Decrypted] {decrypted_message}"
        except Exception as e:
            logger.error(f"Failed to decrypt message: {e}")
            return f"[Decryption Failed] {message}"