import json
from dataclasses import dataclass
from loguru import logger

@dataclass
class InitiationRitePayload:
    """Structured payload for initiation rite memos"""
    username: str
    commitment: str
    version: str = "1.0"

    def to_json(self) -> str:
        """Convert payload to JSON string"""
        return json.dumps(self.__dict__)

    @classmethod
    def from_json(cls, json_str: str) -> 'InitiationRitePayload':
        """Create payload from JSON string"""
        try:
            data = json.loads(json_str)
            return cls(**data)
        except Exception as e:
            logger.error(f"Error parsing initiation rite payload: {e}")
            raise ValueError("Invalid initiation rite payload format")