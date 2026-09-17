"""LLM skill：把一句人话翻译成计分动作。

没有 key / 调用失败 / 返回的不是合法 JSON 时，一律回落到 scorebot 的正则规则，
所以接了 LLM 也不会让现在的用法失灵。

.env 里填：
  DEEPSEEK_API_KEY=sk-xxxxx
  DEEPSEEK_BASE_URL=https://api.deepseek.com   # 可选
  DEEPSEEK_MODEL=deepseek-chat                 # 可选：deepseek-v4-flash / deepseek-v4-pro
"""

from __future__ import annotations

import json
import os
import re
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import requests

from configutil import load_dotenv_file

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
CONTEXT_SIZE = 10  # 上下文窗口：最近 10 条
TIMEOUT = 20
MAX_DELTA = 999

SYSTEM_PROMPT = """你是企业微信群里的积分机器人，只维护六个 BG 的积分：IEG / CDG / TEG / CSIG / WXG / PCG。

群里有人 @你 说一句话，你判断他要干什么，**只输出一行 JSON**，不要解释、不要代码块、不要 markdown 标记。

action 取值：
- "score"：加减分，ops 给出 [{"bg":"IEG","delta":2}]，delta 为整数，正数加分、负数扣分
- "board"：只是想看积分榜
- "reset"：清零；ops 给要清零的 bg（delta 填 0），整群清零就给空数组
- "none"：听不懂、纯闲聊、或者跟积分无关，不要回复

输出格式：
{"action":"score|board|reset|none","ops":[{"bg":"IEG","delta":2}],"say":"一句不超过 20 字的吐槽，可以为空字符串"}

规则：
- 没写分数默认 1 分；中文数字（一分、三分）也算数
- delta 绝对值不超过 999
- 只认这六个 BG；"eg" 这种缩写可合理推断成 IEG，推断不出来就当 none
- 一句话里改多个 BG 就给多个 ops
- say 不要重复用户的话，不要带 emoji 以外的格式标记
"""

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _parse_decision(content: str) -> Optional[dict]:
    """把模型输出抠成 dict；抠不出来返回 None（调用方回落规则）。"""
    if not content:
        return None
    from scorebot import BG_ORDER  # 延迟导入，避免和 scorebot 互相 import
    text = content.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    action = str(data.get("action") or "").strip().lower()
    if action not in ("score", "board", "reset", "none"):
        return None

    ops: List[Tuple[str, int]] = []
    for item in data.get("ops") or []:
        if not isinstance(item, dict):
            continue
        bg = str(item.get("bg") or "").strip().upper()
        if bg not in BG_ORDER:
            continue
        try:
            delta = int(round(float(item.get("delta", 1))))
        except (TypeError, ValueError):
            delta = 1
        delta = max(-MAX_DELTA, min(MAX_DELTA, delta))
        if action == "reset":
            delta = 0
        elif delta == 0:
            delta = 1 if action == "score" else 0
        ops.append((bg, delta))

    if action == "score" and not ops:
        return None

    return {
        "action": action,
        "ops": [{"bg": bg, "delta": delta} for bg, delta in ops],
        "say": str(data.get("say") or "").strip()[:60],
    }


class LlmSkill:
    def __init__(self) -> None:
        load_dotenv_file()
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.base_url = (
            os.getenv("DEEPSEEK_BASE_URL", "").strip() or DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = os.getenv("DEEPSEEK_MODEL", "").strip() or DEFAULT_MODEL
        self._context: Dict[str, deque] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def remember(self, chatid: Any, who: str, text: str) -> None:
        """上下文窗口：每个会话只留最近 CONTEXT_SIZE 条。"""
        if not text:
            return
        buf = self._context.setdefault(str(chatid), deque(maxlen=CONTEXT_SIZE))
        buf.append(f"{who}: {text}")

    def context_lines(self, chatid: Any) -> List[str]:
        return list(self._context.get(str(chatid), deque()))

    def decide(self, chatid: Any, sender: str, text: str) -> Optional[dict]:
        """问模型这句话要干什么；任何异常都返回 None 让规则兜底。"""
        if not self.enabled or not text:
            return None
        messages: List[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        history = self.context_lines(chatid)
        if history:
            messages.append(
                {
                    "role": "user",
                    "content": "群里最近的聊天（供参考，不一定都跟积分有关）：\n"
                    + "\n".join(history),
                }
            )
        messages.append({"role": "user", "content": f"{sender} 说：{text}"})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 200,
            "response_format": {"type": "json_object"},
        }
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=TIMEOUT,
            )
            data = resp.json()
        except Exception as e:
            print("LLM 调用失败，回落规则:", e)
            return None
        if data.get("error"):
            print("LLM 返回错误，回落规则:", data.get("error"))
            return None

        content = (
            ((data.get("choices") or [{}])[0]).get("message") or {}
        ).get("content") or ""
        decision = _parse_decision(content)
        if decision is None:
            print("LLM 输出无法解析，回落规则:", content[:120])
        return decision
