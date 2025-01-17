# Standard imports
from typing import List, Dict, Any, Optional, Union
import traceback
import math
import binascii
from datetime import datetime
import random
import string
from inspect import signature
# Third party imports
from xrpl.models import Memo
from xrpl.utils import str_to_hex
from xrpl.wallet import Wallet
from loguru import logger
from cryptography.fernet import InvalidToken
# Local imports
from pftpyclient.utilities.exceptions import HandshakeRequiredException
from pftpyclient.configuration.constants import MEMO_VERSION
from pftpyclient.utilities.compression import CompressionError, compress_data, decompress_data
from pftpyclient.user_login.credentials import CredentialManager
from pftpyclient.utilities.encryption import MessageEncryption
from pftpyclient.models.models import (
    MemoGroup,
    MemoStructure,
    MemoConstructionParameters,
    MemoDataStructureType,
    MemoTransaction
)
from pftpyclient.configuration.constants import (
    UNIQUE_ID_VERSION, 
    UNIQUE_ID_PATTERN_V1,
    XRP_MEMO_STRUCTURAL_OVERHEAD,
    MAX_CHUNK_SIZE
)

class MemoProcessor:
    """Entry point for memo processing"""
    
    @staticmethod
    def parse_group(
        group: MemoGroup,
        wallet: Optional[Wallet] = None,
        credential_manager: Optional[CredentialManager] = None,
        message_encryption: Optional[MessageEncryption] = None,
        decrypt: bool = True
    ) -> Optional[str]:
        """        
        Parsing occurs in a fixed order:
        1. Unchunk (if chunked)
        2. Decompress (if compressed)
        3. Decrypt (if encrypted and decrypt is True)
        
        For encrypted messages, requires:
        - credential_manager: For accessing private keys
        - message_encryption: For ECDH operations
        - node_config: For determining secret types
        
        Raises ValueError if group is incomplete or parsing fails.
        """
        if not group.memos:
            return None
        
        first_tx = group.memos[0]
        structure = MemoStructure.from_transaction(first_tx)

        if not structure.is_valid_format:
            logger.warning("structure is not valid format")
            return None
    
        if not StandardizedMemoProcessor.validate_group(group):
            logger.warning("Invalid standardized format group")
            return None
        
        return StandardizedMemoProcessor.parse_group(
            group,
            wallet,
            credential_manager=credential_manager,
            message_encryption=message_encryption,
            decrypt=decrypt
        )
        
    @staticmethod
    def construct_group(
        memo_params: MemoConstructionParameters,
        wallet: Optional[Wallet] = None,
        message_encryption: Optional[MessageEncryption] = None,
    ) -> MemoGroup:
        """
        Construct memo(s) from response parameters.
        Processing occurs in a fixed order:
        1. Encrypt (if specified)
        2. Compress (if specified)
        3. Chunk (memos are always chunked)

        Args:
            memo_params: Contains raw memo data and structure
            wallet: Wallet to use for encryption
            message_encryption: Required for encryption

        Returns:
            MemoGroup containing a single Memo or list of Memos if chunked

        Raises:
            ValueError: If encryption is requested but required parameters are missing
        """
        return StandardizedMemoProcessor.construct_group(
            memo_params,
            wallet,
            message_encryption=message_encryption,
        )
    
    @staticmethod
    def estimate_chunks(
        memo_data: str,
        encrypt: bool = False,
        compress: bool = False,
        message_encryption: Optional[MessageEncryption] = None
    ) -> int:
        """Estimate number of chunks needed for a memo without constructing it.
        
        Args:
            memo_data: Raw memo content
            encrypt: Whether memo will be encrypted
            compress: Whether memo will be compressed
            message_encryption: Required if encrypt=True
            
        Returns:
            int: Estimated number of chunks required
            
        Raises:
            ValueError: If encryption is requested but message_encryption not provided
        """
        try:
            # Process data in same order as actual memo construction
            processed_data = memo_data

            # Estimate encryption overhead if needed
            if encrypt:
                if not message_encryption:
                    raise ValueError("message_encryption required for encrypted memos")
                processed_data = message_encryption.get_dummy_encrypted_content(processed_data)

            # Estimate compression if enabled
            if compress:
                try:
                    processed_data = compress_data(processed_data)
                except CompressionError:
                    logger.warning("Compression failed during estimation, using uncompressed size")

            # Create test memo with maximum metadata overhead
            test_memo = Memo(
                memo_format="metadata_overhead_estimate",  # Maximum expected format length
                memo_type="v1.0.0.2024-03-21_12:00__XX99",  # Maximum type length
                memo_data=processed_data
            )

            # Calculate chunks needed
            size_info = calculate_memo_size(
                test_memo.memo_format,
                test_memo.memo_type,
                test_memo.memo_data
            )
            
            max_data_size = MAX_CHUNK_SIZE - size_info['total_size']
            if max_data_size <= 0:
                raise ValueError("Memo overhead exceeds maximum chunk size")

            required_chunks = math.ceil(len(processed_data.encode('utf-8')) / max_data_size)
            return max(1, required_chunks)

        except Exception as e:
            logger.error(f"Error estimating chunks: {e}")
            logger.error(traceback.format_exc())
            raise

def generate_custom_id():
    """ Generate a unique memo_type following the pattern: 'v1.0.0.YYYY-MM-DD_HH:MM__LLNN'
    where LL are random uppercase letters and NN are random numbers.
    
    Example: 'v1.0.2024-03-20_15:30__AB12'
    """
    letters = ''.join(random.choices(string.ascii_uppercase, k=2))
    numbers = ''.join(random.choices(string.digits, k=2))
    second_part = letters + numbers
    date_string = datetime.now().strftime("%Y-%m-%d %H:%M")
    output= f"v{UNIQUE_ID_VERSION}.{date_string}__" + second_part
    output = output.replace(' ',"_")
    return output

def to_hex(string):
    return binascii.hexlify(string.encode()).decode()

def hex_to_text(hex_string):
    bytes_object = bytes.fromhex(hex_string)
    try:
        ascii_string = bytes_object.decode("utf-8")
        return ascii_string
    except UnicodeDecodeError:
        return bytes_object  # Return the raw bytes if it cannot decode as utf-8
    
def construct_encoded_memo(memo_format, memo_type, memo_data):
    """Constructs a memo object with hex-encoded fields, ready for XRPL submission"""
    return Memo(
        memo_data=to_hex(memo_data),
        memo_type=to_hex(memo_type),
        memo_format=to_hex(memo_format)
    )

def encode_memo(memo: Memo) -> Memo:
    """Converts a Memo object with plaintext fields to a Memo object with hex-encoded fields"""
    return Memo(
        memo_data=to_hex(memo.memo_data),
        memo_type=to_hex(memo.memo_type),
        memo_format=to_hex(memo.memo_format)
    )

def decode_memo_fields_to_dict(memo: Union[Memo, dict]) -> Dict[str, Any]:
    """Decodes hex-encoded XRP memo fields from a dictionary to a more readable dictionary format."""
    if hasattr(memo, 'memo_format'):  # This is a Memo object
        fields = {
            'memo_format': memo.memo_format,
            'memo_type': memo.memo_type,
            'memo_data': memo.memo_data
        }
    else:  # This is a dictionary from transaction JSON
        fields = {
            'memo_format': memo.get('MemoFormat', ''),
            'memo_type': memo.get('MemoType', ''),
            'memo_data': memo.get('MemoData', '')
        }
    
    return {
        key: hex_to_text(value or '')
        for key, value in fields.items()
    }

def calculate_memo_size(memo_format: str, memo_type: str, memo_data: str) -> dict:
    """
    Calculates the size components of a memo using consistent logic.
    
    Args:
        memo_format: The format field (usually username)
        memo_type: The type field (usually task_id)
        memo_data: The data field (the actual content)
        
    Returns:
        dict: Size breakdown including:
            - format_size: Size of hex-encoded format
            - type_size: Size of hex-encoded type
            - data_size: Size of hex-encoded data
            - structural_overhead: Fixed overhead for JSON structure
            - total_size: Total size including all components
    """
    format_size = len(str_to_hex(memo_format))
    type_size = len(str_to_hex(memo_type))
    data_size = len(str_to_hex(memo_data))
    structural_overhead = XRP_MEMO_STRUCTURAL_OVERHEAD

    # logger.debug(f"Memo size breakdown:")
    # logger.debug(f"  format_size: {format_size}")
    # logger.debug(f"  type_size: {type_size}")
    # logger.debug(f"  data_size: {data_size}")
    # logger.debug(f"  structural_overhead: {structural_overhead}")
    # logger.debug(f"  total_size: {format_size + type_size + data_size + structural_overhead}")

    return {
        'format_size': format_size,
        'type_size': type_size,
        'data_size': data_size,
        'structural_overhead': structural_overhead,
        'total_size': format_size + type_size + data_size + structural_overhead
    }

def calculate_required_chunks(
        memo: Memo, 
        max_size: int = MAX_CHUNK_SIZE
    ) -> int:
    """
    Calculates how many chunks will be needed to send a memo.
    
    Args:
        memo: Memo object to analyze
        max_size: Maximum size in bytes for each complete Memo object
        
    Returns:
        int: Number of chunks required
        
    Raises:
        ValueError: If the memo cannot be chunked (overhead too large)
    """
    memo_format = memo.memo_format
    memo_type = memo.memo_type
    memo_data = memo.memo_data

    # logger.debug(f"Deconstructed (plaintext) memo sizes: "
    #             f"memo_format: {len(memo_format)}, "
    #             f"memo_type: {len(memo_type)}, "
    #             f"memo_data: {len(memo_data)}")

    # Calculate overhead sizes
    size_info = calculate_memo_size(memo_format, memo_type, "chunk_999__")  # assuming chunk_999__ is worst-case chunk label overhead
    max_data_size = max_size - size_info['total_size']

    # logger.debug(f"Size allocation:")
    # logger.debug(f"  Max size: {max_size}")
    # logger.debug(f"  Total overhead: {size_info['total_size']}")
    # logger.debug(f"  Available for data: {max_size} - {size_info['total_size']} = {max_data_size}")

    if max_data_size <= 0:
        raise ValueError(
            f"No space for data: max_size={max_size}, total_overhead={size_info['total_size']}"
        )
    
    # Calculate number of chunks needed
    data_bytes = memo_data.encode('utf-8')
    required_chunks = math.ceil(len(data_bytes) / max_data_size)
    required_chunks = 1 if required_chunks == 0 else required_chunks
    return required_chunks

def chunk_memos(
        memo: Memo, 
        max_size: int = MAX_CHUNK_SIZE
    ) -> List[Memo]:
    """
    Splits a Memo object into multiple Memo objects, each under MAX_CHUNK_SIZE bytes.
    Updates memo_format with chunk metadata before constructing final Memo objects.
    
    Args:
        memo: Memo object to be chunked
        max_size: Maximum size in bytes for each complete Memo object

    Returns:
        List of unencoded Memo objects ready for final processing
    """
    memo_format = memo.memo_format
    memo_type = memo.memo_type
    memo_data = memo.memo_data

    # Calculate chunks needed and validate size
    num_chunks = calculate_required_chunks(memo, max_size)
    chunk_size = len(memo_data.encode('utf-8')) // num_chunks
            
    # Split into chunks
    chunked_memos = []
    data_bytes = memo_data.encode('utf-8')
    for chunk_number in range(1, num_chunks + 1):
        start_idx = (chunk_number - 1) * chunk_size
        end_idx = start_idx + chunk_size if chunk_number < num_chunks else len(data_bytes)
        chunk = data_bytes[start_idx:end_idx]
        chunk_memo_data = chunk.decode('utf-8', errors='ignore')

        # Debug the sizes
        # test_format = str_to_hex(memo_format)
        # test_type = str_to_hex(memo_type)
        # test_data = str_to_hex(chunk_memo_data)
        
        # logger.debug(f"Chunk {chunk_number} sizes:")
        # logger.debug(f"  Plaintext Format size: {len(memo_format)}")
        # logger.debug(f"  Plaintext Type size: {len(memo_type)}")
        # logger.debug(f"  Plaintext Data size: {len(chunk_memo_data)}")
        # logger.debug(f"  Plaintext Total size: {len(memo_format) + len(memo_type) + len(chunk_memo_data)}")
        # logger.debug(f"  Hex Format size: {len(test_format)}")
        # logger.debug(f"  Hex Type size: {len(test_type)}")
        # logger.debug(f"  Hex Data size: {len(test_data)}")
        # logger.debug(f"  Hex Total size: {len(test_format) + len(test_type) + len(test_data)}")
        
        chunk_memo = Memo(
            memo_format=memo_format,
            memo_type=memo_type,
            memo_data=chunk_memo_data
        )

        chunked_memos.append(chunk_memo)

    return chunked_memos

class StandardizedMemoProcessor:
    """Handles processing of new standardized format memos"""
        
    @staticmethod
    def parse_group(
        group: MemoGroup,
        wallet: Optional[Wallet] = None,
        credential_manager: Optional[CredentialManager] = None,
        message_encryption: Optional[MessageEncryption] = None,
        decrypt: bool = True
    ) -> str:
        """
        Parse a complete group of standardized format memos.
        The memo_format (e.g., "e.b.c1/4") indicates which processing steps are needed:
        - 'c' indicates chunking
        - 'b' indicates brotli compression
        - 'e' indicates ECDH encryption
        
        Parsing occurs in a fixed order:
        1. Unchunk (if chunked)
        2. Decompress (if compressed)
        3. Decrypt (if encrypted)
        
        For encrypted messages, requires:
        - credential_manager: For accessing private keys
        - message_encryption: For ECDH operations
        - node_config: For determining secret types
        
        Raises ValueError if group is incomplete or parsing fails.
        """
        if not group.memos:
            raise ValueError("Empty group")
        
        structure = MemoStructure.from_transaction(group.memos[0])
        if not structure.is_valid_format:
            raise ValueError("Not a standardized format group")
        
        # For chunked messages, verify completeness and join
        if structure.is_chunked:
            if not structure.total_chunks:
                raise ValueError("Chunked message missing total_chunks")
                
            # Verify we have all chunks
            chunk_indices = group.chunk_indices
            if len(chunk_indices) != structure.total_chunks:
                raise ValueError(f"Missing chunks. Have {len(chunk_indices)}/{structure.total_chunks}")
                
            # Sort and join chunks
            sorted_msgs = sorted(
                group.memos,
                key=lambda tx: MemoStructure.from_transaction(tx).chunk_index or 0
            )
            
            processed_data = ''
            for tx in sorted_msgs:
                processed_data += tx.memo_data
                
        else:
            # Single message
            processed_data = group.memos[0].memo_data
        
        # Apply decompression if specified
        if structure.compression_type == MemoDataStructureType.BROTLI:
            try:
                processed_data = decompress_data(processed_data)
            except CompressionError as e:
                logger.error(f"Decompression failed for group {group.group_id}: {e}")
                raise
                
        # Handle encryption if specified
        if structure.encryption_type == MemoDataStructureType.ECDH and decrypt:
            if not all([credential_manager, message_encryption]):
                logger.warning(
                    f"Cannot decrypt memo {group.group_id} - missing required parameters. "
                    f"Need credential_manager: {bool(credential_manager)}, "
                    f"message_encryption: {bool(message_encryption)}"
                )
                return processed_data

            # Get channel details from first transaction
            first_tx = group.memos[0]

            # Channel addresses and channel counterparties vary depending on the direction of the message
            # For example, if the message is from the node to the user, the account is the node's address and the destination is the user's address
            # But the channel address must always be the user's address
            if first_tx.destination == wallet.address:
                channel_address = first_tx.destination
                channel_counterparty = first_tx.account
            else: 
                channel_address = first_tx.account
                channel_counterparty = first_tx.destination

            try:
                # Get handshake keys
                channel_key, counterparty_key = message_encryption.get_handshake_for_address(
                    channel_address=channel_address,
                    channel_counterparty=channel_counterparty
                )
                if not (channel_key and counterparty_key):
                    logger.warning("Cannot decrypt message - no handshake found")
                    return processed_data

                # Get shared secret using credential manager's API
                shared_secret = credential_manager.get_shared_secret(received_key=counterparty_key)
                processed_data = message_encryption.process_encrypted_message(
                    processed_data, 
                    shared_secret
                )
            except Exception as e:
                logger.error(
                    f"StandardizedMemoProcessor.process_group: Error decrypting message {group.group_id} "
                    f"between address {channel_address} and counterparty {channel_counterparty}: {e}"
                )
                logger.error(traceback.format_exc())
                return f"[Decryption Failed] {processed_data}"
            
        return processed_data
    
    @staticmethod
    def validate_group(group: MemoGroup) -> bool:
        """
        Validate that all messages in the group have consistent structure.
        """
        if not group.memos:
            return False
            
        first_structure = MemoStructure.from_transaction(group.memos[0])
        if not first_structure.is_valid_format:
            return False
            
        # Check all messages have same format
        for msg in group.memos[1:]:
            structure = MemoStructure.from_transaction(msg)
            if not structure.is_valid_format:
                return False
                
            if (structure.encryption_type != first_structure.encryption_type or
                structure.compression_type != first_structure.compression_type or
                structure.total_chunks != first_structure.total_chunks):
                return False
                
        return True
    
    def construct_final_memo(
        memo_format_prefix: str,  # e.g., "v1.e.b" or "v1.-.-"
        memo_type: str,
        memo_data: str,
        chunk_info: Optional[tuple[int, int]] = None  # (chunk_number, total_chunks)
    ) -> Memo:
        """
        Constructs the final memo with complete format string.
        
        Args:
            memo_format_prefix: Partial format string with version and processing flags
            memo_type: The memo type/group id
            memo_data: The processed memo data
            chunk_info: Optional tuple of (chunk_number, total_chunks)
        
        Returns:
            Memo with complete format string
        """
        # Finalize format string with chunk information
        if chunk_info:
            chunk_number, total_chunks = chunk_info
            memo_format = f"{memo_format_prefix}.c{chunk_number}/{total_chunks}"
        else:
            memo_format = f"{memo_format_prefix}.-"
            
        return construct_encoded_memo(
            memo_format=memo_format,
            memo_type=memo_type,
            memo_data=memo_data
        )
    
    @staticmethod
    def construct_group(
        memo_params: MemoConstructionParameters,
        wallet: Optional[Wallet] = None,
        message_encryption: Optional[MessageEncryption] = None
    ) -> MemoGroup:
        """
        Construct standardized format memo(s) from response parameters.
        Processing occurs in a fixed order:
        1. Encrypt (if specified)
        2. Compress (if specified)
        3. Chunk (memos are always chunked)
        4. Final hex encoding for XRPL submission

        Args:
            response_params: Contains raw memo data and structure
            credential_manager: Required for encryption
            message_encryption: Required for encryption
            node_config: Required for encryption

        Returns:
            MemoGroup containing a single Memo or list of Memos if chunked

        Raises:
            ValueError: If encryption is requested but required parameters are missing
        """
        processed_data = memo_params.memo_data
        memo_type = memo_params.memo_type or generate_custom_id()

        # Handle encryption if specified
        encryption_type = MemoDataStructureType.NONE.value
        if memo_params.should_encrypt:
            if not all([wallet, message_encryption]):
                raise ValueError("Missing required parameters for encryption")

            try:
                # Get handshake keys
                channel_key, counterparty_key = message_encryption.get_handshake_for_address(
                    channel_address=memo_params.source,
                    channel_counterparty=memo_params.destination
                )
                if not (channel_key and counterparty_key):
                    raise HandshakeRequiredException(memo_params.source, memo_params.destination)
                
                # Get shared secret and encrypt
                shared_secret = message_encryption.get_shared_secret(
                    received_public_key=counterparty_key,
                    channel_private_key=wallet.seed
                )
                processed_data = message_encryption.encrypt_memo(
                    processed_data,
                    shared_secret
                )
                encryption_type = MemoDataStructureType.ECDH.value
            except Exception as e:
                logger.error(f"StandardizedMemoProcessor.construct_group: Error encrypting memo: {e}")
                raise

        # Handle compression if specified
        compression_type = MemoDataStructureType.NONE.value
        if memo_params.should_compress:
            try:
                processed_data = compress_data(processed_data)
                compression_type = MemoDataStructureType.BROTLI.value
            except CompressionError as e:
                logger.error(f"StandardizedMemoProcessor.construct_group: Error compressing memo: {e}")
                raise

        # Create base unencoded Memo
        base_memo = Memo(
            memo_format=f"{MemoDataStructureType.VERSION.value}{MEMO_VERSION}.{encryption_type}.{compression_type}",  # Format prefix
            memo_type=memo_type,
            memo_data=processed_data
        )

        # Get chunked memos
        chunked_memos = chunk_memos(base_memo)
        
        # Construct final memos with complete memo_format strings
        memos = []
        for idx, memo in enumerate(chunked_memos, 1):
            memo_format = f"{memo.memo_format}.c{idx}/{len(chunked_memos)}"
            memo = construct_encoded_memo(
                memo_format=memo_format,
                memo_type=memo.memo_type,
                memo_data=memo.memo_data
            )
            memos.append(memo)

        return MemoGroup.create_from_memos(memos=memos)

class MemoGroupProcessor:
    """Utility class for processing memo groups from transaction history"""

    @staticmethod
    def get_latest_valid_memo_groups(
        memo_history: List[Dict],
        num_groups: Optional[int] = 1
    ) -> Optional[Union[MemoGroup, List[MemoGroup]]]:
        """Get the most recent valid MemoGroup from a set of memo records.
        
        Args:
            memo_history: List of dictionaries containing memo records from SQLManager
            num_groups: Optional int limiting the number of memo groups to return.
                       If 1 (default), returns a single MemoGroup.
                       If > 1, returns a list of up to num_groups MemoGroups.
                       If 0 or None, returns all valid memo groups.

        Returns:
            Optional[Union[MemoGroup, List[MemoGroup]]]: Most recent valid MemoGroup(s) or None if no valid groups found
        """
        if not memo_history:
            return None

        # Filter for successful transactions
        filtered_records = [
            record for record in memo_history 
            if record.get('transaction_result') == "tesSUCCESS"
        ]

        if not filtered_records:
            return None
        
        # Get valid MemoTransaction fields
        valid_fields = set(signature(MemoTransaction).parameters.keys())

        valid_groups = []
        
        # Group by memo_type to handle chunked memos
        memo_types = {record.get('memo_type') for record in filtered_records}
        
        for memo_type in memo_types:
            try:
                # Get all transactions for this memo_group
                group_txs = [
                    tx for tx in filtered_records 
                    if tx.get('memo_type') == memo_type
                ]

                # Convert dictionary records to MemoTransaction objects
                memo_txs = []
                for tx in group_txs:
                    valid_tx = {k: v for k, v in tx.items() if k in valid_fields}
                    memo_txs.append(MemoTransaction(**valid_tx))

                # Create and validate MemoGroup
                memo_group = MemoGroup.create_from_memos(memo_txs)

                # Additional check to ensure we only accept standardized memos
                if not memo_group.structure or not memo_group.structure.is_valid_format:
                    # logger.warning(f"Skipping memo group {memo_type} - not using standardized format")
                    continue

                valid_groups.append(memo_group)

                # Return early if we've reached the desired number of groups
                if num_groups and len(valid_groups) == num_groups:
                    break
            
            except ValueError as e:
                logger.warning(f"Failed to process memo group {memo_type}: {e}")
                continue

        # If no valid memo groups found, return None
        if not valid_groups:
            return None
        
        # Return a single MemoGroup if num_groups is 1, otherwise return a list
        return valid_groups[0] if num_groups == 1 else valid_groups