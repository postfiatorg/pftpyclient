import base64
import brotli
import string
from typing import Optional
from loguru import logger

class CompressionError(ValueError):
    """Raised when compression or decompression fails"""
    pass

def compress_data(input_string: str) -> str:
    """
    Compress a string using Brotli and encode as base64.
    
    Args:
        input_string: String to compress
        
    Returns:
        str: Base64-encoded compressed string
        
    Raises:
        CompressionError: If compression fails
    """
    try:
        # Compress using Brotli
        compressed_data = brotli.compress(input_string.encode('utf-8'))
        # Encode to base64
        base64_encoded = base64.b64encode(compressed_data)
        # Convert to string
        return base64_encoded.decode('utf-8')
    except Exception as e:
        raise CompressionError(f"Failed to compress data: {e}")

def decompress_data(compressed_string: str) -> str:
    """
    Decompress a base64-encoded, brotli-compressed string.
    Includes legacy error correction for malformed base64.
    
    Args:
        compressed_string: The compressed string to decompress
        
    Returns:
        str: The decompressed string
        
    Raises:
        CompressionError: If decompression fails after all correction attempts
    """
    def try_decompress(attempt_string: str) -> Optional[str]:
        """Helper function to attempt decompression"""
        try:
            base64_decoded = base64.b64decode(attempt_string)
            decompressed = brotli.decompress(base64_decoded)
            return decompressed.decode('utf-8')
        except Exception:
            return None
            
    # Try original string first
    if result := try_decompress(compressed_string):
        return result
        
    # Clean string of invalid base64 characters
    valid_chars = set(string.ascii_letters + string.digits + '+/=')
    cleaned = ''.join(c for c in compressed_string if c in valid_chars)
    
    # Try with different padding lengths
    for i in range(4):
        padded = cleaned + ('=' * i)
        if result := try_decompress(padded):
            return result

    raise CompressionError(
        "Failed to decompress string after all correction attempts. "
        "Original string may be corrupted or incorrectly encoded."
    )