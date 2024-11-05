from enum import Enum
from functools import wraps
from loguru import logger

class WalletState(Enum):
    UNFUNDED = "unfunded"               # XRP address exists but not activated on XRPL
    FUNDED = "funded"                   # XRP address activated on XRPL
    TRUSTLINED = "trustlined"           # Trust line to PFT established
    INITIATED = "initiated"             # Initiation rite sent
    GOOGLE_DOC_SENT = "google_doc_sent" # Google doc sent
    ACTIVE = "active"                   # Fully initialized, ready to accept tasks

# states where account exists on blockchain
FUNDED_STATES = [state for state in WalletState if state != WalletState.UNFUNDED]
# states where trust line is established
TRUSTLINED_STATES = [WalletState.TRUSTLINED, WalletState.INITIATED, WalletState.GOOGLE_DOC_SENT, WalletState.ACTIVE]
# states where initiation rite is sent
INITIATED_STATES = [WalletState.INITIATED, WalletState.GOOGLE_DOC_SENT, WalletState.ACTIVE]
# states where google doc is sent
GOOGLE_DOC_SENT_STATES = [WalletState.GOOGLE_DOC_SENT, WalletState.ACTIVE]
# states where PFT features are available, after genesis is sent
PFT_STATES = [WalletState.ACTIVE]

def requires_wallet_state(required_states):
    """
    Decorator that silently skips function execution if wallet is not in required state(s).
    Can be used with both PostFiatTaskManager and WalletApp methods.
    
    Args:
        required_states: WalletState or list of WalletState
    """
    if not isinstance(required_states, (list, tuple)):
        required_states = [required_states]

    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            # Handle both WalletApp and PostFiatTaskManager
            wallet_state = getattr(self, 'wallet_state', None)
            if wallet_state is None and hasattr(self, 'task_manager'):
                wallet_state = self.task_manager.wallet_state
            
            if wallet_state not in required_states:
                logger.debug(f"Wallet state is {wallet_state}, but {required_states} are required to run {func.__name__}")
                return
            return func(self, *args, **kwargs)
        return wrapper
    return decorator