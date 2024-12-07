from enum import Enum
from pftpyclient.configuration.constants import SystemMemoType
from pftpyclient.configuration.configuration import NetworkConfig
from decimal import Decimal
from typing import Optional

class AddressType(Enum):
    """Types of special addresses"""
    NODE = "Node"
    REMEMBRANCER = "Remembrancer"
    ISSUER = "Issuer"
    OTHER = "Other"

PFT_REQUIREMENTS = {
    AddressType.NODE: 1,
    AddressType.REMEMBRANCER: 1,
    AddressType.ISSUER: 0,
    AddressType.OTHER: 0
}

class TransactionRequirementService:
    """Service for transaction requirements"""

    def __init__(self, network_config: NetworkConfig):
        self.network_config = network_config

        # Base PFT requirements by address type
        self.base_pft_requirements = {
            AddressType.NODE: Decimal('1'),
            AddressType.REMEMBRANCER: Decimal('1'),
            AddressType.ISSUER: Decimal('0'),
            AddressType.OTHER: Decimal('0')
        }

    def get_address_type(self, address: str) -> AddressType:
        """Get the type of address."""
        if address == self.network_config.node_address:
            return AddressType.NODE
        elif address == self.network_config.remembrancer_address:
            return AddressType.REMEMBRANCER
        elif address == self.network_config.issuer_address:
            return AddressType.ISSUER
        else:
            return AddressType.OTHER
        
    def get_pft_requirement(self, address: str, memo_type: Optional[str] = None) -> Decimal:
        """Get the PFT requirement for an address.
        
        Args:
            address: XRPL address to check
            memo_type: Optional memo type to consider
            
        Returns:
            Decimal: PFT requirement for the address
        """
        # System memos (like handshakes) don't require PFT
        if memo_type and memo_type in [type.value for type in SystemMemoType]:
            return Decimal('0')
        
        # Otherwise, use base requirements by address type
        return self.base_pft_requirements[self.get_address_type(address)]
    
    def is_node_address(self, address: str) -> bool:
        """Check if address is a node address"""
        return self.get_address_type(address) == AddressType.NODE
    
    def is_remembrancer_address(self, address: str) -> bool:
        """Check if address is a remembrancer address"""
        return self.get_address_type(address) == AddressType.REMEMBRANCER
    
    def is_issuer_address(self, address: str) -> bool:
        """Check if address is the issuer address"""
        return self.get_address_type(address) == AddressType.ISSUER