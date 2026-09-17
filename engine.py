"""Learning and reply loops — platform-agnostic.

One fetch loop fans out messages so learning and reply do not steal from each other.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any, Dict, List, Optional

from adapter import BotAdapter, IncomingMessage
from configutil import load_config
from message import is_empty, keep_text_and_image, parse_answer_text, strip_image_id
from wordstock import WordStock


def _should_stop(cfg: dict) -> bool:
    return int(cfg.get("stopsign", 0)) == 1


def _pick_weighted(answers: List[dict]) -> Optional[dict]:
    if not answers:
        return None
    weights = []
    for a in answers:
        try:
            w = max(int(a.get("same", 1)), 1)
        except (TypeError, ValueError):
            w = 1
        weights.append(w)
    return random.choices(answers, weights=weights, k=1)[0]


def _chance(percent: int) -> bool:
    return random.uniform(0, 1) <= max(0, min(100, int(percent))) * 0.01


class Learner:
    """Chain consecutive group messages into Q→A pairs (interval gap resets)."""

    def __init__(self, stock: Optional[WordStock] = None):
        self.stock = stock or WordStock()
        self._sign: Dict[Any, dict] = {}

    def handle(self, msg: IncomingMessage, interval: int) -> None:
        if not msg.is_group:
            return
        chain = keep_text_and_image(msg.chain)
        if is_empty(chain):
            return
        group = msg.group_id
        mid = msg.message_id
        ts = int(msg.timestamp)
        state = self._sign.setdefault(group, {"id": None, "signtime": 0, "befor": None})
        if state["id"] is None or ts - state["signtime"] > interval:
            state["id"] = mid
        if mid == state["id"]:
            self.stock.record_question(group, chain)
            state["signtime"] = ts
            state["befor"] = chain
        else:
            recorded = self.stock.record_question(group, chain)
            if state["befor"] is not None:
                self.stock.record_answer(group, state["befor"], recorded)
            state["signtime"] = ts
            state["befor"] = recorded


class Replier:
    """Exact-match reply with weighted random answer (Plain + Image only)."""

    def __init__(self, adapter: BotAdapter, stock: Optional[WordStock] = None):
        self.adapter = adapter
        self.stock = stock or WordStock()

    def handle(self, msg: IncomingMessage, replychance: int) -> None:
        if not msg.is_group:
            return
        chain = keep_text_and_image(msg.chain)
        if is_empty(chain):
            return
        answers = self.stock.get_answers(msg.group_id, chain)
        if not answers:
            return
        if not _chance(replychance):
            print("已匹配答案，但概率未触发")
            return
        picked = _pick_weighted(answers)
        if not picked:
            return
        out = strip_image_id(parse_answer_text(picked.get("answertext")))
        if is_empty(out):
            return
        mid = self.adapter.send_chain(msg.group_id, out, is_group=True)
        print(f"答案已发送 {out} id={mid}")


class Engine:
    """Single poll loop: learn and/or reply based on config flags."""

    def __init__(self, adapter: BotAdapter, stock: Optional[WordStock] = None):
        self.adapter = adapter
        self.stock = stock or WordStock()
        self.learner = Learner(self.stock)
        self.replier = Replier(adapter, self.stock)
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("引擎已启动（文本/图片）")

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            cfg = load_config()
            if _should_stop(cfg):
                print("引擎收到停止信号")
                self._running = False
                break
            learning_on = int(cfg.get("learning", 0)) == 1
            reply_on = int(cfg.get("reply", 0)) == 1
            if not learning_on and not reply_on:
                time.sleep(0.5)
                continue
            learn_groups = set(cfg.get("learninggrouplist") or [])
            reply_groups = set(cfg.get("replygrouplist") or [])
            interval = int(cfg.get("interval", 900))
            chance = int(cfg.get("replychance", 100))
            try:
                messages = self.adapter.fetch_messages()
            except Exception as e:
                print("拉取消息异常:", e)
                time.sleep(1)
                continue
            for msg in messages:
                if learning_on and msg.group_id in learn_groups:
                    try:
                        self.learner.handle(msg, interval)
                    except Exception as e:
                        print("学习处理异常:", e)
                if reply_on and msg.group_id in reply_groups:
                    try:
                        self.replier.handle(msg, chance)
                    except Exception as e:
                        print("回复处理异常:", e)
            time.sleep(0.5)
        print("引擎已停止")
