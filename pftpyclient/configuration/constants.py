from enum import Enum
from decimal import Decimal

# Pftpyclient runtime constants
UPDATE_TIMER_INTERVAL_SEC = 60  # 60 Seconds
REFRESH_GRIDS_AFTER_TASK_DELAY_SEC = 5  # 5 seconds

# XRPL constants
DEFAULT_PFT_LIMIT = 100_000_000
MIN_XRP_PER_TRANSACTION = Decimal('0.000001')
MAX_CHUNK_SIZE = 1024
XRP_MEMO_STRUCTURAL_OVERHEAD = 100  # JSON structure, quotes, etc.

class SystemMemoType(Enum):
    HANDSHAKE = 'HANDSHAKE'
    INITIATION_RITE = 'INITIATION_RITE'
    GOOGLE_DOC_CONTEXT_LINK = 'google_doc_context_link'

SYSTEM_MEMO_TYPES = [memo_type.value for memo_type in SystemMemoType]

# Task types where the memo_type = task_id, requiring further disambiguation in the memo_data
class TaskType(Enum):
    REQUEST_POST_FIAT = 'REQUEST_POST_FIAT ___ '
    PROPOSAL = 'PROPOSED PF ___ '
    ACCEPTANCE = 'ACCEPTANCE REASON ___ '
    REFUSAL = 'REFUSAL REASON ___ '
    TASK_OUTPUT = 'COMPLETION JUSTIFICATION ___ '
    VERIFICATION_PROMPT = 'VERIFICATION PROMPT ___ '
    VERIFICATION_RESPONSE = 'VERIFICATION RESPONSE ___ '
    REWARD = 'REWARD RESPONSE __ '

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
