"""Message-chain helpers. Only Plain text and Image are kept."""

from __future__ import annotations

import copy
from typing import Any, Iterable, List, Optional

SUPPORTED_TYPES = frozenset({"Plain", "Image"})


def keep_text_and_image(chain: Iterable[dict]) -> List[dict]:
    """Keep only Plain and Image nodes."""
    result = []
    for item in chain or []:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "Plain":
            text = item.get("text", "")
            if text is None:
                text = ""
            result.append({"type": "Plain", "text": str(text)})
        elif t == "Image":
            node = {"type": "Image"}
            if item.get("url"):
                node["url"] = item["url"]
            if item.get("path"):
                node["path"] = item["path"]
            if item.get("base64"):
                node["base64"] = item["base64"]
            # Keep at least one payload so the image is sendable later.
            if len(node) > 1:
                result.append(node)
    return result


def strip_volatile(chain: Iterable[dict]) -> List[dict]:
    """Remove fields that change between deliveries (url for Image keys)."""
    cleaned = copy.deepcopy(list(chain or []))
    for item in cleaned:
        if not isinstance(item, dict):
            continue
        item.pop("url", None)
        item.pop("imageId", None)
    return cleaned


def chain_key(chain: Iterable[dict]) -> str:
    """Stable string key for a question/answer lookup."""
    return str(strip_volatile(keep_text_and_image(chain)))


def strip_image_id(chain: Iterable[dict]) -> List[dict]:
    cleaned = copy.deepcopy(list(chain or []))
    for item in cleaned:
        if isinstance(item, dict):
            item.pop("imageId", None)
    return cleaned


def plain_text(chain: Iterable[dict]) -> Optional[str]:
    for item in chain or []:
        if isinstance(item, dict) and item.get("type") == "Plain":
            return item.get("text")
    return None


def is_empty(chain: Iterable[dict]) -> bool:
    return len(keep_text_and_image(chain)) == 0


def parse_answer_text(answertext: Any) -> List[dict]:
    """Deserialize stored answertext (legacy used str(list))."""
    if isinstance(answertext, list):
        return keep_text_and_image(answertext)
    if isinstance(answertext, str):
        try:
            value = eval(answertext, {"__builtins__": {}}, {})
        except Exception:
            return [{"type": "Plain", "text": answertext}]
        if isinstance(value, list):
            return keep_text_and_image(value)
        return [{"type": "Plain", "text": str(value)}]
    return [{"type": "Plain", "text": str(answertext)}]
