"""Config helpers."""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict

_lock = threading.Lock()

DEFAULT_CONFIG: Dict[str, Any] = {
    "learning": 0,
    "reply": 0,
    "learninggrouplist": [],
    "replygrouplist": [],
    "interval": 900,
    "replychance": 100,
    "stopsign": 0,
}


def load_json(path: str, default: Any = None) -> Any:
    with _lock:
        if not os.path.exists(path):
            return default if default is not None else {}
        with open(path, "r", encoding="utf-8-sig") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                f.seek(0)
                return eval(f.read(), {"__builtins__": {}}, {})


def save_json(path: str, obj: Any) -> None:
    with _lock:
        with open(path, "w", encoding="utf-8-sig") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)


def load_config(path: str = "config.json") -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    # Prefer config.json; fall back to legacy config.clc once.
    if os.path.exists(path):
        loaded = load_json(path, {})
    elif os.path.exists("config.clc"):
        loaded = load_json("config.clc", {})
        save_json(path, {**cfg, **{k: loaded.get(k, cfg[k]) for k in cfg}})
        loaded = load_json(path, {})
    else:
        save_json(path, cfg)
        return cfg
    for k, v in cfg.items():
        if k not in loaded:
            loaded[k] = v
    return loaded


def save_config(cfg: Dict[str, Any], path: str = "config.json") -> None:
    save_json(path, cfg)
