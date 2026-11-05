"""Asynchronous typed Mailbox and Event Bus for inter-agent communication (IPC)."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field


class AgentMessage(BaseModel):
    """Structured message transmitted across the inter-agent event bus."""

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sender_id: str = Field(description="Conversation/Agent ID of the transmitting entity.")
    recipient_id: str = Field(description="Conversation/Agent ID of the receiving entity, or '*' for broadcast.")
    topic: str = Field(default="general", description="Message topic or signal category.")
    payload: Any = Field(description="Typed or arbitrary message data.")
    timestamp: float = Field(default_factory=time.time)


class EventBus:
    """Non-blocking, asynchronous reactive mailbox and message router.

    Eliminates busy-polling loops by utilizing reactive async queues per agent.
    """

    def __init__(self) -> None:
        self._mailboxes: dict[str, asyncio.Queue[AgentMessage]] = {}
        self._subscribers: dict[str, set[str]] = {}  # topic -> set of agent_ids

    def register(self, agent_id: str) -> None:
        """Register an agent's mailbox on the event bus."""
        if agent_id not in self._mailboxes:
            self._mailboxes[agent_id] = asyncio.Queue()

    def unregister(self, agent_id: str) -> None:
        """Unregister an agent and clean up its mailbox and topic subscriptions."""
        self._mailboxes.pop(agent_id, None)
        for topic_subscribers in self._subscribers.values():
            topic_subscribers.discard(agent_id)

    def subscribe(self, agent_id: str, topic: str) -> None:
        """Subscribe an agent to a broadcast topic."""
        self.register(agent_id)
        if topic not in self._subscribers:
            self._subscribers[topic] = set()
        self._subscribers[topic].add(agent_id)

    async def send(self, message: AgentMessage) -> bool:
        """Deliver a message to a specific agent's mailbox or broadcast to subscribers.

        Returns:
            True if delivered to at least one recipient, False otherwise.
        """
        if message.recipient_id == "*":
            # Broadcast to all registered agents or topic subscribers
            recipients = self._subscribers.get(message.topic, set(self._mailboxes.keys()))
            delivered = False
            for r_id in recipients:
                if r_id != message.sender_id and r_id in self._mailboxes:
                    await self._mailboxes[r_id].put(message)
                    delivered = True
            return delivered

        # Point-to-point direct message
        if message.recipient_id in self._mailboxes:
            await self._mailboxes[message.recipient_id].put(message)
            return True

        return False

    async def receive(self, agent_id: str, timeout: float | None = None) -> AgentMessage:
        """Retrieve the next message from an agent's mailbox, awaiting reactively without polling.

        Args:
            agent_id: The recipient agent's ID.
            timeout: Optional maximum seconds to wait before raising TimeoutError.

        Returns:
            Next AgentMessage in the queue.
        """
        self.register(agent_id)
        queue = self._mailboxes[agent_id]

        if timeout is not None:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        return await queue.get()

    def pending_count(self, agent_id: str) -> int:
        """Return the number of unread messages in an agent's mailbox."""
        if agent_id in self._mailboxes:
            return self._mailboxes[agent_id].qsize()
        return 0
