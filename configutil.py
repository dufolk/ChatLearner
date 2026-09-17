"""Config / storage helpers (score bot)."""

from __future__ import annotations

import json
import os
import threading
from typing import Any

_lock = threading.Lock()


def load_dotenv_file(path: str = ".env") -> None:
    """Load KEY=VALUE pairs from .env into os.environ (existing wins)."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def load_json(path: str, default: Any = None) -> Any:
    with _lock:
        if not os.path.exists(path):
            return default if default is not None else {}
        with open(path, "r", encoding="utf-8-sig") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return default if default is not None else {}


def save_json(path: str, obj: Any) -> None:
    with _lock:
        with open(path, "w", encoding="utf-8-sig") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
