from enum import Enum

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