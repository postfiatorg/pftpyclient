from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, List

from pftpyclient.models.models import MemoGroup
from pftpyclient.models.memo_processor import MemoProcessor
from pftpyclient.configuration.constants import TaskType, UNIQUE_ID_PATTERN_V1

@dataclass
class Task:
    """Represents a task and its complete lifecycle state.
    
    Each field corresponds to a specific TaskType and contains the message content
    for that state, if it exists. The task progresses through these states in order,
    though some states may be skipped (e.g., a task might be refused instead of accepted).
    """
    task_id: str
    task_request: str
    request_datetime: datetime

    proposal: Optional[str] = None
    proposal_datetime: Optional[datetime] = None
    acceptance: Optional[str] = None
    acceptance_datetime: Optional[datetime] = None
    refusal: Optional[str] = None
    refusal_datetime: Optional[datetime] = None
    task_completion: Optional[str] = None
    task_completion_datetime: Optional[datetime] = None
    verification_prompt: Optional[str] = None
    verification_prompt_datetime: Optional[datetime] = None
    verification_response: Optional[str] = None
    verification_response_datetime: Optional[datetime] = None
    reward: Optional[str] = None
    reward_datetime: Optional[datetime] = None
    pft_amount: Decimal = Decimal(0)

    @property
    def current_state(self) -> TaskType:
        """Determine the current state of the task based on which fields are populated"""
        if self.reward:
            return TaskType.REWARD
        if self.verification_response:
            return TaskType.VERIFICATION_RESPONSE
        if self.verification_prompt:
            return TaskType.VERIFICATION_PROMPT
        if self.task_completion:
            return TaskType.TASK_COMPLETION
        if self.refusal:
            return TaskType.REFUSAL
        if self.acceptance:
            return TaskType.ACCEPTANCE
        if self.proposal:
            return TaskType.PROPOSAL
        return TaskType.TASK_REQUEST
    
    @classmethod
    async def from_memo_groups(cls, memo_groups: List[MemoGroup]) -> 'Task':
        """Create a Task from a list of MemoGroups.
        
        Args:
            memo_groups: List of MemoGroups related to this task
            
        Returns:
            Task: Constructed task object
            
        Raises:
            ValueError: If no TASK_REQUEST is found in the memo groups
        """
        sorted_groups = sorted(memo_groups, key=lambda g: g.memos[0].datetime, reverse=True)

        request_group = next(
            (g for g in sorted_groups if g.group_id.endswith(TaskType.TASK_REQUEST.value)),
            None
        )
        if not request_group:
            raise ValueError("No TASK_REQUEST found in memo groups")
        
        task_id = cls.extract_task_id(request_group.group_id)
        request = await MemoProcessor.parse_group(request_group)
        if not request:
            raise ValueError(f"Could not parse request from group {request_group.group_id}")
        
        # Initialize task with required fields
        task = cls(
            task_id=task_id,
            task_request=request,
            request_datetime=request_group.memos[0].datetime
        )

        # Process all other groups
        for group in sorted_groups:
            if group == request_group:
                continue

            content = await MemoProcessor.parse_group(group)
            if not content:
                continue

            # Get state type from memo_type suffix
            state_type = TaskType(group.group_id.split('__')[-1])
            datetime = group.memos[0].datetime

            # Set the appropriate field based on state type
            match state_type:
                case TaskType.PROPOSAL:
                    task.proposal = content
                    task.proposal_datetime = datetime
                case TaskType.ACCEPTANCE:
                    task.acceptance = content
                    task.acceptance_datetime = datetime
                case TaskType.REFUSAL:
                    task.refusal = content
                    task.refusal_datetime = datetime
                case TaskType.TASK_COMPLETION:
                    task.task_completion = content
                    task.completion_datetime = datetime
                case TaskType.VERIFICATION_PROMPT:
                    task.verification_prompt = content
                    task.verification_prompt_datetime = datetime
                case TaskType.VERIFICATION_RESPONSE:
                    task.verification_response = content
                    task.verification_response_datetime = datetime
                case TaskType.REWARD:
                    task.reward = content
                    task.reward_response_datetime = datetime
                    task.pft_amount = group.pft_amount

        return task
    
    @staticmethod
    def extract_task_id(memo_type: str) -> str:
        """Extract the task ID from a memo type string"""
        match = UNIQUE_ID_PATTERN_V1.match(memo_type)
        if not match:
            raise ValueError(f"Invalid memo_type format: {memo_type}")
        return match.group()
    
    def to_dict(self) -> dict:
        """Convert task to dictionary format for grid display"""
        return {
            'task_id': self.task_id,
            'request': self.task_request,
            'proposal': self.proposal or '',
            'response': self._format_response(),
            'datetime': self.request_datetime.isoformat()
        }

    def _format_response(self) -> str:
        """Format the task's response based on its current state"""
        if self.refusal:
            return f"REFUSED: {self.refusal}"
        elif self.acceptance:
            return f"ACCEPTED: {self.acceptance}"
        return ''
    
    def to_verification_dict(self) -> dict:
        """Convert task to verification display format"""
        return {
            'task_id': self.task_id,
            'proposal': self.proposal or '',
            'verification': self.verification_prompt or '',
            'datetime': (
                self.verification_prompt_datetime.isoformat() 
                if self.verification_prompt_datetime 
                else self.request_datetime.isoformat()
            )
        }

    def to_reward_dict(self) -> dict:
        """Convert task to reward display format"""
        return {
            'task_id': self.task_id,
            'proposal': self.proposal or '',
            'reward': self.reward or '',
            'payout': float(self.pft_amount),
            'datetime': (
                self.reward_datetime.isoformat() 
                if self.reward_datetime 
                else self.request_datetime.isoformat()
            )
        }

    @property
    def is_verification_pending(self) -> bool:
        """Check if task is pending verification"""
        return (
            self.verification_prompt is not None and 
            self.verification_response is None and
            self.refusal is None
        )

    @property
    def is_rewarded(self) -> bool:
        """Check if task has been rewarded"""
        return self.reward is not None and self.pft_amount > 0