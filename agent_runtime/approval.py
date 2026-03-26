"""Approval flow for agent actions."""

import asyncio
from dataclasses import dataclass, field
from enum import Enum


class ApprovalStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class PendingAction:
    action_id: str
    agent_name: str
    room_id: str
    description: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    event: asyncio.Event = field(default_factory=asyncio.Event)


class ApprovalManager:
    def __init__(self):
        self._pending: dict[str, PendingAction] = {}
        self._counter = 0

    def create_action(self, agent_name: str, room_id: str, description: str) -> PendingAction:
        self._counter += 1
        action_id = f"action-{self._counter}"
        action = PendingAction(
            action_id=action_id,
            agent_name=agent_name,
            room_id=room_id,
            description=description,
        )
        self._pending[action_id] = action
        return action

    def approve(self, action_id: str) -> bool:
        if action_id in self._pending:
            action = self._pending[action_id]
            action.status = ApprovalStatus.APPROVED
            action.event.set()
            return True
        return False

    def reject(self, action_id: str) -> bool:
        if action_id in self._pending:
            action = self._pending[action_id]
            action.status = ApprovalStatus.REJECTED
            action.event.set()
            return True
        return False

    async def wait_for_decision(self, action_id: str, timeout: float = 300) -> ApprovalStatus:
        if action_id not in self._pending:
            return ApprovalStatus.REJECTED
        action = self._pending[action_id]
        try:
            await asyncio.wait_for(action.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            action.status = ApprovalStatus.REJECTED
        return action.status

    def format_approval_message(self, action: PendingAction) -> str:
        return (
            f"**Approval Required**\n\n"
            f"{action.description}\n\n"
            f"Reply with:\n"
            f"- `approve {action.action_id}` to approve\n"
            f"- `reject {action.action_id}` to reject"
        )
