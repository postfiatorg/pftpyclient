from enum import Enum
from dataclasses import dataclass
from typing import List, Optional
from decimal import Decimal
from pftpyclient.configuration.configuration import ConfigurationManager

DEFAULT_PFT_LIMIT = 100_000_000

class AddressType(Enum):
    """Types of special addresses"""
    NODE = "Node"   # Each node has an address
    REMEMBRANCER = "Remembrancer"  # Each node may have a separate address for its remembrancer
    ISSUER = "Issuer"  # There's only one PFT issuer per L1 network
    OTHER = "Other"  # Any other address type, including users

# PFT requirements by address type
# TODO: Make this dynamic based on operation
PFT_REQUIREMENTS = {
    AddressType.NODE: 1,
    AddressType.REMEMBRANCER: 1,
    AddressType.ISSUER: 0,
    AddressType.OTHER: 0
}

# TODO: Move this out of constants.py
@dataclass
class NetworkConfig:
    """Configuration for an XRPL network (mainnet or testnet)"""
    name: str
    node_name: str
    node_address: str
    remembrancer_name: str
    remembrancer_address: str
    issuer_address: str
    websockets: List[str]
    public_rpc_url: str
    discord_guild_id: int
    discord_activity_channel_id: int
    explorer_tx_url_mask: str
    explorer_account_url_mask: str
    local_rpc_url: Optional[str] = None

    def get_address_type(self, address: str) -> AddressType:
        """Get the type of address"""
        if address == self.node_address:
            return AddressType.NODE
        elif address == self.remembrancer_address:
            return AddressType.REMEMBRANCER
        elif address == self.issuer_address:
            return AddressType.ISSUER
        else:
            return AddressType.OTHER
        
    def get_pft_requirement(self, address: str) -> Decimal:
        """Get the PFT requirement for an address"""
        return Decimal(PFT_REQUIREMENTS[self.get_address_type(address)])
    
XRPL_MAINNET = NetworkConfig(
    name="mainnet",
    node_name="postfiatfoundation",
    node_address="r4yc85M1hwsegVGZ1pawpZPwj65SVs8PzD",
    remembrancer_name="postfiatfoundation_remembrancer",
    remembrancer_address="rJ1mBMhEBKack5uTQvM8vWoAntbufyG9Yn",
    issuer_address="rnQUEEg8yyjrwk9FhyXpKavHyCRJM9BDMW",
    websockets=[
        "wss://xrplcluster.com", 
        "wss://xrpl.ws/", 
        "wss://s1.ripple.com/", 
        "wss://s2.ripple.com/"
    ],
    public_rpc_url="https://s2.ripple.com:51234",
    local_rpc_url='http://127.0.0.1:5005',
    discord_guild_id=1061800464045310053,
    discord_activity_channel_id=1239280089699450920,
    explorer_tx_url_mask='https://livenet.xrpl.org/transactions/{hash}/detailed',
    explorer_account_url_mask='https://livenet.xrpl.org/accounts/{address}'
)

XRPL_TESTNET = NetworkConfig(
    name="testnet",
    node_name="postfiatfoundation_testnet",
    node_address="rUWuJJLLSH5TUdajVqsHx7M59Vj3P7giQV",
    remembrancer_name="postfiatfoundation_testnet_remembrancer",
    remembrancer_address="rN2oaXBhFE9urGN5hXup937XpoFVkrnUhu",
    issuer_address="rLX2tgumpiUE6kjr757Ao8HWiJzC8uuBSN",
    websockets=[
        "wss://s.altnet.rippletest.net:51233"
    ],
    public_rpc_url="https://s.altnet.rippletest.net:51234",
    local_rpc_url=None,  # No local node for testnet yet
    discord_guild_id=510536760367906818,
    discord_activity_channel_id=1308884322199277699,
    explorer_tx_url_mask='https://testnet.xrpl.org/transactions/{hash}/detailed',
    explorer_account_url_mask='https://testnet.xrpl.org/accounts/{address}'
)

class Network(Enum):
    XRPL_MAINNET = XRPL_MAINNET
    XRPL_TESTNET = XRPL_TESTNET

# Helper function to get current network config
def get_network_config(network: Optional[Network] = None) -> NetworkConfig:
    """Get network configuration based on Network enum.
    
    Args:
        network: Optional Network enum value. If None, uses configuration setting
                to determine network.
        
    Returns:
        NetworkConfig: Configuration for the specified network
    """
    if network is None:
        config = ConfigurationManager()
        use_testnet = config.get_global_config('use_testnet')
        network = Network.XRPL_TESTNET if use_testnet else Network.XRPL_MAINNET

    return network.value

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
    USER_GENESIS = 'USER GENESIS __ '  # TODO: Remove

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
