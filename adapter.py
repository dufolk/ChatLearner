"""Platform adapter interface.

Implement BotAdapter for Discord / Telegram / OneBot / etc.
Message chains use Plain / Image dicts (see message.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional


@dataclass
class IncomingMessage:
    group_id: Any
    sender_id: Any
    message_id: Any
    timestamp: int
    chain: List[dict]
    raw: Any = None
    is_group: bool = True


class BotAdapter(ABC):
    """Minimal surface needed by the learning / reply engine."""

    @abstractmethod
    def connect(self) -> None:
        """Establish session / login."""

    @abstractmethod
    def fetch_messages(self) -> List[IncomingMessage]:
        """Poll or drain pending messages. Return [] when idle."""

    @abstractmethod
    def send_chain(
        self,
        target: Any,
        chain: List[dict],
        *,
        is_group: bool = True,
    ) -> Optional[Any]:
        """Send a Plain/Image message chain. Return platform message id if any."""

    def close(self) -> None:
        pass
