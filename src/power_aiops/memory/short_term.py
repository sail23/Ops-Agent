from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Turn:
    agent_id: str
    content: str
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ShortTermMemory:
    """Doc 4.2.2: sliding window (default 20 turns)."""

    def __init__(self, max_turns: int = 20) -> None:
        self._max = max_turns
        self._turns: deque[Turn] = deque(maxlen=max_turns)

    def append(self, agent_id: str, content: str) -> None:
        self._turns.append(Turn(agent_id=agent_id, content=content))

    def recent(self) -> list[Turn]:
        return list(self._turns)

    def summary_placeholder(self) -> str:
        """Wire to LLM summarization later."""
        if not self._turns:
            return ""
        return f"[summary of {len(self._turns)} turns — plug LLM here]"
