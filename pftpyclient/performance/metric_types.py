from enum import Enum
from typing import Optional

class Metric(Enum):
    DURATION = ('duration', 'ms')
    MEMORY = ('memory', 'bytes')
    CPU = ('cpu', 'percent')
    COUNT = ('count', 'count')
    QUEUE_SIZE = ('queue_size', 'count')

    def __init__(self, type_name: str, unit: str):
        self.type_name = type_name
        self.unit = unit

    @classmethod
    def from_type_name(cls, type_name: str) -> Optional['Metric']:
        """Convert a type name back to a Metric enum"""
        for metric in cls:
            if metric.type_name == type_name:
                return metric
        return None
