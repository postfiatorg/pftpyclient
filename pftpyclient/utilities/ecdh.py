import nacl.bindings
from xrpl.core import addresscodec
from xrpl.core.keypairs.ed25519 import ED25519
from loguru import logger

class ECDHUtils:
    """Handles ECDH key exchange"""

    @staticmethod
    def _get_raw_entropy(wallet_seed: str) -> bytes:
        """Returns the raw entropy bytes from the specified wallet secret"""
        decoded_seed = addresscodec.decode_seed(wallet_seed)
        return decoded_seed[0]
    
    @staticmethod
    def get_ecdh_public_key_from_seed(wallet_seed: str) -> str:
        """
        Get ECDH public key directly from a wallet seed
        
        Args:
            wallet_seed: The wallet seed to derive the key from
            
        Returns:
            str: The ECDH public key in hex format
            
        Raises:
            ValueError: If wallet_seed is invalid
        """
        try:
            raw_entropy = ECDHUtils._get_raw_entropy(wallet_seed)
            public_key, _ = ED25519.derive_keypair(raw_entropy, is_validator=False)
            return public_key
        except Exception as e:
            logger.error(f"ECDHUtils.get_ecdh_public_key_from_seed: Failed to derive ECDH public key: {e}")
            raise ValueError(f"Failed to derive ECDH public key: {e}") from e
        
    @staticmethod
    def get_shared_secret(received_public_key: str, channel_private_key: str) -> bytes:
        """
        Derive a shared secret using ECDH
        
        Args:
            received_public_key: public key received from another party
            channel_private_key: Seed for the wallet to derive the shared secret

        Returns:
            bytes: The derived shared secret

        Raises:
            ValueError: if received_public_key is invalid or channel_private_key is invalid
        """
        try:
            raw_entropy = ECDHUtils._get_raw_entropy(channel_private_key)
            return ECDHUtils._derive_shared_secret(public_key_hex=received_public_key, seed_bytes=raw_entropy)
        except Exception as e:
            logger.error(f"ECDHUtils.get_shared_secret: Failed to derive shared secret: {e}")
            raise ValueError(f"Failed to derive shared secret: {e}") from e
        
    @staticmethod
    def _derive_shared_secret(public_key_hex: str, seed_bytes: bytes) -> bytes:
        """
        Derive a shared secret using ECDH
        Args:
            public_key_hex: their public key in hex
            seed_bytes: original entropy/seed bytes (required for ED25519)
        Returns:
            bytes: The shared secret
        """
        # First derive the ED25519 keypair using XRPL's method
        public_key_raw, private_key_raw = ED25519.derive_keypair(seed_bytes, is_validator=False)
        
        # Convert private key to bytes and remove ED prefix
        private_key_bytes = bytes.fromhex(private_key_raw)
        if len(private_key_bytes) == 33 and private_key_bytes[0] == 0xED:
            private_key_bytes = private_key_bytes[1:]  # Remove the ED prefix
        
        # Convert public key to bytes and remove ED prefix
        public_key_self_bytes = bytes.fromhex(public_key_raw)
        if len(public_key_self_bytes) == 33 and public_key_self_bytes[0] == 0xED:
            public_key_self_bytes = public_key_self_bytes[1:]  # Remove the ED prefix
        
        # Combine private and public key for NaCl format (64 bytes)
        private_key_combined = private_key_bytes + public_key_self_bytes
        
        # Convert their public key
        public_key_bytes = bytes.fromhex(public_key_hex)
        if len(public_key_bytes) == 33 and public_key_bytes[0] == 0xED:
            public_key_bytes = public_key_bytes[1:]  # Remove the ED prefix
        
        # Convert ED25519 keys to Curve25519
        private_curve = nacl.bindings.crypto_sign_ed25519_sk_to_curve25519(private_key_combined)
        public_curve = nacl.bindings.crypto_sign_ed25519_pk_to_curve25519(public_key_bytes)
        
        # Use raw X25519 function
        shared_secret = nacl.bindings.crypto_scalarmult(private_curve, public_curve)

        return shared_secret