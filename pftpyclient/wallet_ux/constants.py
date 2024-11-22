from enum import Enum

DEFAULT_PFT_LIMIT = 100_000_000

# MAINNET CONSTANTS
DEFAULT_NODE_NAME = 'postfiatfoundation'
DEFAULT_NODE_ADDRESS = "r4yc85M1hwsegVGZ1pawpZPwj65SVs8PzD"
REMEMBRANCER_ADDRESS = "rJ1mBMhEBKack5uTQvM8vWoAntbufyG9Yn"
ISSUER_ADDRESS = "rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW"
TREASURY_WALLET_ADDRESS = "r46SUhCzyGE4KwBnKQ6LmDmJcECCqdKy4q"

MAINNET_WEBSOCKETS = [
    "wss://xrplcluster.com",
    "wss://xrpl.ws/",
    "wss://s1.ripple.com/",
    "wss://s2.ripple.com/"
]

MAINNET_URL = "https://s2.ripple.com:51234"

MAINNET_EXPLORER_URL = "https://livenet.xrpl.org"
MAINNET_EXPLORER_TRANSACTION_URL = "https://livenet.xrpl.org/transactions/{hash}/detailed"
MAINNET_EXPLORER_ACCOUNT_URL = "https://livenet.xrpl.org/accounts/{address}"

# TESTNET CONSTANTS
TESTNET_DEFAULT_NODE_NAME = "postfiatfoundation_testnet"
TESTNET_DEFAULT_NODE_ADDRESS = "rUWuJJLLSH5TUdajVqsHx7M59Vj3P7giQV"
TESTNET_REMEMBRANCER_ADDRESS = "rN2oaXBhFE9urGN5hXup937XpoFVkrnUhu"
TESTNET_ISSUER_ADDRESS = "rLX2tgumpiUE6kjr757Ao8HWiJzC8uuBSN"

TESTNET_WEBSOCKETS = [
    "wss://s.altnet.rippletest.net:51233"
]

TESTNET_URL = "https://s.altnet.rippletest.net:51234"

TESTNET_EXPLORER_URL = "https://testnet.xrpl.org"
TESTNET_EXPLORER_TRANSACTION_URL = "https://testnet.xrpl.org/transactions/{hash}/detailed"
TESTNET_EXPLORER_ACCOUNT_URL = "https://testnet.xrpl.org/accounts/{address}"

# SPECIAL ADDRESS ATTRIBUTES - WIP
SPECIAL_ADDRESSES = {
    REMEMBRANCER_ADDRESS: {
        "memo_pft_requirement": 1,
        "display_text": "Post Fiat Network Remembrancer"
    },
    ISSUER_ADDRESS: {
        "memo_pft_requirement": 0,
        "display_text": "Post Fiat Token Issuer"
    }
}

# DEPRECATED - TODO: remove
CREDENTIAL_FILENAME = "manyasone_cred_list.txt"

class SystemMemoType(Enum):
    HANDSHAKE = 'HANDSHAKE'
    INITIATION_RITE = 'INITIATION_RITE'
    GOOGLE_DOC_CONTEXT_LINK = 'google_doc_context_link'

SYSTEM_MEMO_TYPES = [memo_type.value for memo_type in SystemMemoType]

# Task types where the memo_type = task_id, requiring further disambiguation in the memo_data
class TaskType(Enum):
    REQUEST_POST_FIAT = 'REQUEST_POST_FIAT ___'
    PROPOSAL = 'PROPOSED PF ___'
    ACCEPTANCE = 'ACCEPTANCE REASON ___'
    REFUSAL = 'REFUSAL REASON ___'
    TASK_OUTPUT = 'COMPLETION JUSTIFICATION ___'
    VERIFICATION_PROMPT = 'VERIFICATION PROMPT ___'
    VERIFICATION_RESPONSE = 'VERIFICATION RESPONSE ___'
    REWARD = 'REWARD RESPONSE __'
    USER_GENESIS = 'USER GENESIS __'

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

class MessageType(Enum):
    MEMO = 'chunk_'

# Helper to get all message indicators
MESSAGE_INDICATORS = [message_type.value for message_type in MessageType]
