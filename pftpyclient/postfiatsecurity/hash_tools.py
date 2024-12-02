import secrets
from base64 import urlsafe_b64encode as b64e, urlsafe_b64decode as b64d

from cryptography.fernet import Fernet
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from hashlib import sha256, new as new_hash
import nacl.bindings
import nacl.signing
from xrpl.core.keypairs.ed25519 import ED25519

backend = default_backend()
iterations = 100_000

def _derive_key(password: bytes, salt: bytes, iterations: int = iterations) -> bytes:
    """Derive a secret key from a given password and salt"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt,
        iterations=iterations, backend=backend)
    return b64e(kdf.derive(password))

def password_encrypt(message: bytes, password: str, iterations: int = iterations) -> bytes:
    salt = secrets.token_bytes(16)
    key = _derive_key(password.encode(), salt, iterations)
    return b64e(
        b'%b%b%b' % (
            salt,
            iterations.to_bytes(4, 'big'),
            b64d(Fernet(key).encrypt(message)),
        )
    )

def password_decrypt(token: bytes, password: str) -> bytes:
    ''' use:
    decrypted_message = password_decrypt(encrypted_message, password)
    '''
    decoded = b64d(token)
    salt, iter, token = decoded[:16], decoded[16:20], b64e(decoded[20:])
    iterations = int.from_bytes(iter, 'big')
    key = _derive_key(password.encode(), salt, iterations)
    return Fernet(key).decrypt(token)

def get_account_id(public_key_hex: str) -> bytes:
    """Convert a public key to an account ID (a 20-byte identifier)"""
    # Convert hex to bytes
    public_key_bytes = bytes.fromhex(public_key_hex)

    # SHA256 of the public key
    sha256_hash = sha256(public_key_bytes).digest()

    # RIPEMD160 of the SHA256 hash
    ripemd160_hash = new_hash('ripemd160')
    ripemd160_hash.update(sha256_hash)
    account_id = ripemd160_hash.digest()

    return account_id

def derive_shared_secret(public_key_hex: str, seed_bytes: bytes) -> bytes:
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