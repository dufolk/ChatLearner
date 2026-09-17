"""企业微信群机器人 Webhook 发送。

只要把「群机器人」加进目标群、拿到 key，就能无 @、无需事先交互地往该群推消息。
这是「不@」方案里唯一干净稳定的发消息通道（aibot_send_msg 需要会话先被 @ 过一次）。
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Dict, List, Optional

import requests

from configutil import load_config

SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
MAX_IMAGE = 2 * 1024 * 1024  # webhook 图片上限 2MB


def _env_default_key() -> str:
    import os

    return os.getenv("WECOM_WEBHOOK_KEY", "").strip()


def resolve_key(target: Any, cfg: Optional[dict] = None) -> Optional[str]:
    """把 群名 / roomid / chatid 解析成 webhook key。

    优先 config.json 的 webhook_keys，支持三种写法：
      {"6组！": "<key>"}                      按群名
      {"wrXXXX": "<key>"}                     按存档 roomid 或机器人 chatid
      {"6组！": "<key>"} + group_aliases{"6组！": "wrXXXX"}   别名反查
    """
    cfg = load_config() if cfg is None else cfg
    keys: Dict[str, str] = cfg.get("webhook_keys") or {}
    tid = str(target)

    if keys.get(tid):
        return keys[tid]

    aliases = cfg.get("group_aliases") or {}
    for name, mapped in aliases.items():
        if mapped and str(mapped) == tid and keys.get(name):
            return keys[name]
    return _env_default_key() or None


def post(key: str, payload: dict) -> dict:
    resp = requests.post(
        SEND_URL.format(key=key),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=20,
    )
    data = resp.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"webhook 发送失败: {data}")
    return data


def send_markdown(key: str, content: str) -> dict:
    return post(key, {"msgtype": "markdown", "markdown": {"content": content}})


def send_image(key: str, data: bytes) -> dict:
    if len(data) > MAX_IMAGE:
        raise RuntimeError(f"图片超过 webhook 2MB 上限（{len(data)} bytes）")
    return post(
        key,
        {
            "msgtype": "image",
            "image": {
                "base64": base64.b64encode(data).decode("ascii"),
                "md5": hashlib.md5(data).hexdigest(),
            },
        },
    )


def _image_bytes(node: dict) -> Optional[bytes]:
    if node.get("base64"):
        try:
            return base64.b64decode(node["base64"])
        except Exception:
            return None
    path = node.get("path")
    if path:
        import os

        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
    url = node.get("url")
    if url:
        try:
            return requests.get(url, timeout=20).content
        except Exception as e:
            print("webhook 下载图片失败:", e)
    return None


def send_chain(key: str, chain: List[dict]) -> List[dict]:
    """Plain 合并成一条 markdown，Image 逐条发送。"""
    texts = [n.get("text", "") for n in chain if n.get("type") == "Plain"]
    images = [n for n in chain if n.get("type") == "Image"]
    acks = []
    if texts:
        acks.append(send_markdown(key, "\n".join(texts)))
    for img in images:
        data = _image_bytes(img)
        if not data:
            print("跳过无法取到数据的图片")
            continue
        acks.append(send_image(key, data))
    return acks
