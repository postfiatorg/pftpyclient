from enum import Enum
from decimal import Decimal
import re

# Pftpyclient runtime constants
UPDATE_TIMER_INTERVAL_SEC = 60  # 60 Seconds
REFRESH_GRIDS_AFTER_TASK_DELAY_SEC = 5  # 5 seconds

# XRPL constants
DEFAULT_PFT_LIMIT = 100_000_000
MIN_XRP_PER_TRANSACTION = Decimal('0.000001')
MAX_CHUNK_SIZE = 1024
XRP_MEMO_STRUCTURAL_OVERHEAD = 100  # JSON structure, quotes, etc.

# Versioning Constants
MEMO_VERSION = "1.0"
UNIQUE_ID_VERSION = "1.0"  # Unique ID pattern for memo types
UNIQUE_ID_PATTERN_V1 = re.compile(fr'(v{UNIQUE_ID_VERSION}\.(?:\d{{4}}-\d{{2}}-\d{{2}}_\d{{2}}:\d{{2}}(?:__[A-Z0-9]{{2,4}})?))')

class PFTSendDistribution(Enum):
    """Strategy for distributing PFT across chunked memos"""
    DISTRIBUTE_EVENLY = "distribute_evenly"
    LAST_CHUNK_ONLY = "last_chunk_only"
    FULL_AMOUNT_EACH = "full_amount_each"

class SystemMemoType(Enum):
    INITIATION_REWARD = 'INITIATION_REWARD'  # name is memo_type, value is memo_data pattern
    HANDSHAKE = 'HANDSHAKE'
    HANDSHAKE_RESPONSE = 'HANDSHAKE_RESPONSE'
    INITIATION_RITE = 'INITIATION_RITE'
    GOOGLE_DOC_CONTEXT_LINK = 'google_doc_context_link'

SYSTEM_MEMO_TYPES = [memo_type.value for memo_type in SystemMemoType]

class TaskType(Enum):
    TASK_REQUEST = "TASK_REQUEST"
    PROPOSAL = "PROPOSAL"
    ACCEPTANCE = "ACCEPTANCE"
    REFUSAL = "REFUSAL"
    TASK_COMPLETION = "TASK_COMPLETION"
    VERIFICATION_PROMPT = "VERIFICATION_PROMPT"
    VERIFICATION_RESPONSE = "VERIFICATION_RESPONSE"
    REWARD = "REWARD"

# Additional patterns for specific task types
TASK_PATTERNS = {
    TaskType.PROPOSAL: [" .. ", TaskType.PROPOSAL.value],  # Include both patterns
    # Add any other task types that might have multiple patterns
}

# Default patterns for other task types
for task_type in TaskType:
    if task_type not in TASK_PATTERNS:
        TASK_PATTERNS[task_type] = [task_type.value]

# Helper to get all task indicators
TASK_INDICATORS = [task_type.value for task_type in TaskType]

# TODO: Examine the scope of this enum. It's currently used to identify messages 
# TODO: Should it also be used to identify operations needed to perform on a message? 
# TODO: I.e. unchunking, decompression, decryption, etc.
class MessageType(Enum):
    MEMO = 'chunk_'

# Helper to get all message indicators
MESSAGE_INDICATORS = [message_type.value for message_type in MessageType]
