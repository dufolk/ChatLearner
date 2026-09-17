"""Placeholder adapter. Replace this with a real platform implementation.

In main.py:

    from my_platform_adapter import MyPlatformAdapter
    adapter = MyPlatformAdapter(...)
"""

from __future__ import annotations

from typing import Any, List, Optional

from adapter import BotAdapter, IncomingMessage


class ExampleAdapter(BotAdapter):
    def connect(self) -> None:
        print("ExampleAdapter: 尚未接入真实平台，请实现 BotAdapter")

    def fetch_messages(self) -> List[IncomingMessage]:
        # Convert platform events into IncomingMessage with:
        #   [{"type": "Plain", "text": "你好"}]
        #   [{"type": "Image", "url": "https://..."}]
        #   [{"type": "Image", "path": "/abs/path.png"}]
        return []

    def send_chain(
        self,
        target: Any,
        chain: List[dict],
        *,
        is_group: bool = True,
    ) -> Optional[Any]:
        for node in chain:
            if node.get("type") == "Plain":
                print(f"[send text -> {target}]", node.get("text"))
            elif node.get("type") == "Image":
                print(f"[send image -> {target}]", node.get("url") or node.get("path"))
        return None
