from typing import Union
import binascii
from dataclasses import dataclass
from pftpyclient.configuration.constants import SystemMemoType, TaskType, MessageType
from xrpl.models.amounts import Memo
from loguru import logger
import random
import string
from datetime import datetime
import re

@dataclass
class MemoContent:
    """Intermediate data class for building memos"""
    memo_data: str
    memo_type: Union[SystemMemoType, str]  # Either a SystemMemoType or custom ID string
    user: str  # memo_format

    @staticmethod
    def generate_custom_id() -> str:
        """ Generate a custom ID in the format YYYY-MM-DD_HH:MM__XX99 """
        letters = ''.join(random.choices(string.ascii_uppercase, k=2))
        numbers = ''.join(random.choices(string.digits, k=2))
        second_part = letters + numbers
        date_string = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        output= date_string+'__'+second_part
        output = output.replace(' ',"_")
        return output
    
    @staticmethod
    def is_valid_custom_id(id_str: str) -> bool:
        """ Validate if string matches custom ID pattern"""
        id_pattern = re.compile(r'(\d{4}-\d{2}-\d{2}_\d{2}:\d{2}(?:__[A-Z0-9]{4})?)')
        return bool(re.match(id_pattern, id_str))
    
    def __post_init__(self):
        """ Validate memo_type after initialization """
        if not isinstance(self.memo_type, SystemMemoType):
            if not self.is_valid_custom_id(self.memo_type):
                raise ValueError(f"Invalid memo type: {self.memo_type}. Must be either SystemMemoType or a valid custom ID")

class MemoBuilder:
    @staticmethod
    def to_hex(string: str) -> str:
        """Convert string to hex format"""
        return binascii.hexlify(string.encode()).decode()
    
    @staticmethod
    def is_over_1kb(string: str) -> bool:
        return len(string.encode('utf-8')) > 1024
    
    @staticmethod
    def validate_size(memo_data: str) -> bool:
        """Validate memo_data size (1KB limit)"""
        if MemoBuilder.is_over_1kb(memo_data):
            raise ValueError("Memo_data exceeds 1KB limit")
        return True
    
    @classmethod
    def build_memo(cls, memo_content: MemoContent) -> Memo:
        """Base method for constructing memos"""
        cls.validate_size(memo_content.memo_data)

        # Convert memo_type to string if it's an enum
        memo_type_str = (
            memo_content.memo_type.value
            if isinstance(memo_content.memo_type, SystemMemoType)
            else str(memo_content.memo_type)
        )

        return Memo(
            memo_data=cls.to_hex(memo_content.memo_data),
            memo_type=cls.to_hex(memo_type_str),
            memo_format=cls.to_hex(memo_content.user)
        )
    
    @classmethod
    def build_system_memo(cls, user: str, memo_type: SystemMemoType, memo_data: str) -> Memo:
        """Build system-level memos (handshake, initiation, google doc link, etc.)"""
        memo_content = MemoContent(
            user=user,
            memo_type=memo_type,
            memo_data=memo_data
        )
        return cls.build_memo(memo_content)

    @classmethod
    def build_task_memo(cls, user: str, task_type: TaskType, memo_data: str) -> Memo:
        """Build task-related memos with auto-generated custom ID"""
        memo_content = MemoContent(
            user=user,
            memo_data=f"{task_type.value} {}
        )