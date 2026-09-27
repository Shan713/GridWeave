"""Framework-agnostic agent base class.

Agents here are plain Python objects: deterministic domain logic with an
explicit lifecycle. An orchestration framework (a simulation loop, LangGraph,
AutoGen, ...) can wrap them as nodes, but it is never the source of truth.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from gridweave.utils.logging import get_logger


@dataclass(frozen=True)
class AgentEvent:
    """One entry of an agent's audit trail (for demos, debugging, dashboards)."""

    agent_id: str
    kind: str
    timestamp: datetime | None
    data: Mapping[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    def __init__(self, agent_id: str, event_log_size: int = 1000) -> None:
        self.agent_id = agent_id
        self._events: deque[AgentEvent] = deque(maxlen=event_log_size)
        self.logger = get_logger(f"agents.{agent_id}")

    @abstractmethod
    def observe(self, observation: Any) -> None:
        """Perceive the environment."""

    @abstractmethod
    def snapshot(self) -> Mapping[str, Any]:
        """Return a JSON-serialisable view of the agent's current state."""

    def record(self, kind: str, timestamp: datetime | None = None, **data: Any) -> None:
        self._events.append(AgentEvent(self.agent_id, kind, timestamp, data))
        self.logger.debug("%s %s", kind, data)

    @property
    def events(self) -> list[AgentEvent]:
        return list(self._events)
