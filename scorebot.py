"""BG 积分 bot。

只做一件事：维护 IEG / CDG / TEG / CSIG / WXG / PCG 六个 BG 的积分榜。
群里 @机器人 说「IEG加1分」「TEG扣2分」，它回一条 markdown 榜单。
"""

from __future__ import annotations

import os
import random
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from configutil import load_json, save_json
from eggs import EggBox, flavor_lines, silent_lines
from llm_skill import LlmSkill

SCORE_FILE = os.getenv("SCORE_FILE", "scores.json")
BG_ORDER = ["IEG", "CDG", "TEG", "CSIG", "WXG", "PCG"]
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
MAX_DELTA = 999
LOG_LIMIT = 200
BAR_UNIT = "▉"
BAR_WIDTH = 10

HELP_TEXT = """**BG 积分榜 · 用法**

> IEG加1分 / 给TEG加3分 / WXG +5 → 加分
> CSIG扣2分 / PCG减一分 → 扣分
> WXG加2分 PCG扣1分 → 一次改多个
> 积分榜 / 排行榜 / 分数 → 只看榜
> IEG清零 / 清零 → 单个或全部归零
> help → 这条说明（只有写了 help 才会弹教程，听不懂的它不回）

没写分数时按 1 分算，支持中文数字，单次最多 ±999，
一句里带问号或「多少」的按聊天处理，不改分。"""

_BG_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(BG_ORDER) + r")(?![A-Za-z0-9])", re.I
)
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_RE = re.compile(r"[一二两三四五六七八九十]{1,3}(?=\s*分|\s*$)")
_ADD_RE = re.compile(r"加|\+|add|plus", re.I)
_SUB_RE = re.compile(r"扣|减|扣减|minus|sub|deduct", re.I)
_QUERY_RE = re.compile(r"积分榜|排行榜|榜单|积分|分数|几分|多少分|排名|score|board|rank", re.I)
_RESET_RE = re.compile(r"清零|重置|归零|reset|clear", re.I)
_ASK_RE = re.compile(r"[?？]|吗|呢|多少|怎么|谁")
_HELP_RE = re.compile(r"^(help|帮助|\?|？|怎么用|说明)$", re.I)


def _cn_to_int(token: str) -> Optional[int]:
    if token in _CN_DIGIT:
        return _CN_DIGIT[token]
    if "十" not in token:
        return None
    left, _, right = token.partition("十")
    tens = _CN_DIGIT.get(left, 1) * 10 if left else 10
    ones = _CN_DIGIT.get(right, 0) if right else 0
    return tens + ones


def _find_number(tail: str) -> Optional[float]:
    """在操作符之后的一小段里找数字，找不到返回 None（调用方按 1 分算）。"""
    hit = _NUM_RE.search(tail)
    if hit:
        return float(hit.group(0))
    hit = _CN_RE.search(tail)
    if hit:
        value = _cn_to_int(hit.group(0))
        if value is not None:
            return float(value)
    return None


def parse_ops(text: str) -> List[Tuple[str, int]]:
    """从随便一句人话里抠出 [("IEG", 3), ("WXG", -1)] 这样的加减分动作。"""
    matches = list(_BG_RE.finditer(text))
    ops: List[Tuple[str, int]] = []
    for i, m in enumerate(matches):
        start = matches[i - 1].end() if i else 0
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        window = text[start:end]
        hits = [(hit.start(), 1) for hit in _ADD_RE.finditer(window)]
        hits += [(hit.start(), -1) for hit in _SUB_RE.finditer(window)]
        if not hits:
            continue
        # 一句里有多个 BG 时，取离本 BG 最近的那个操作符，别串到上一段头上。
        rel_start, rel_end = m.start() - start, m.end() - start

        def _distance(pos: int) -> int:
            if pos < rel_start:
                return rel_start - pos
            if pos > rel_end:
                return pos - rel_end
            return 0

        hits.sort(key=lambda h: (_distance(h[0]), h[0]))
        pos, sign = hits[0]
        raw = _find_number(window[pos : pos + 8])
        value = 1 if raw is None else int(round(raw))
        value = max(1, min(MAX_DELTA, value)) * sign
        ops.append((m.group(1).upper(), value))
    return ops


def _ranks(board: Dict[str, int]) -> Dict[str, int]:
    """并列同名次：12 / 12 / 8 记作第 1 / 第 1 / 第 3。"""
    ordered = sorted(BG_ORDER, key=lambda bg: (-board.get(bg, 0), BG_ORDER.index(bg)))
    ranks: Dict[str, int] = {}
    place = 0
    prev: Optional[int] = None
    for idx, bg in enumerate(ordered, 1):
        score = board.get(bg, 0)
        if score != prev:
            place = idx
            prev = score
        ranks[bg] = place
    return ranks


def _fmt(score: int) -> str:
    return f"{score:+d}" if score else "0"


def render_board(chatid: Any, board: Dict[str, int], header: Optional[List[str]] = None) -> str:
    """渲染成普通 markdown：标题加粗 + 有序列表 + 长度条。"""
    ranks = _ranks(board)
    max_abs = max([abs(board.get(bg, 0)) for bg in BG_ORDER] + [1])
    ordered = sorted(BG_ORDER, key=lambda bg: (ranks[bg], BG_ORDER.index(bg)))

    lines = ["**BG 积分榜**"]
    if header:
        lines.extend(header)
        lines.append("")
    for bg in ordered:
        score = board.get(bg, 0)
        prefix = MEDALS.get(ranks[bg], f"{ranks[bg]}.")
        bar = BAR_UNIT * int(round(abs(score) / max_abs * BAR_WIDTH))
        marks = f"  {bar}" if bar else ""
        lines.append(f"{prefix} {bg} **{_fmt(score)}**{marks}")
    return "\n".join(lines)


class ScoreBot:
    def __init__(self, path: str = SCORE_FILE):
        self.path = path
        self.last_chatid: Optional[str] = None
        self._lock = threading.Lock()
        raw = load_json(path, {}) or {}
        self._boards: Dict[str, Dict[str, int]] = {
            str(k): dict(v) for k, v in (raw.get("boards") or {}).items()
        }
        self._log: List[dict] = list(raw.get("log") or [])
        self._eggs = EggBox()
        self._llm = LlmSkill()
        if self._llm.enabled:
            print(f"LLM skill 已启用：{self._llm.model}")
        else:
            print("LLM skill 未启用（没填 DEEPSEEK_API_KEY），走正则规则")

    def board(self, chatid: Any) -> Dict[str, int]:
        key = str(chatid)
        with self._lock:
            data = self._boards.setdefault(key, {bg: 0 for bg in BG_ORDER})
            for bg in BG_ORDER:
                data.setdefault(bg, 0)
            return dict(data)

    def _save(self) -> None:
        save_json(self.path, {"boards": self._boards, "log": self._log[-LOG_LIMIT:]})

    def apply(self, chatid: Any, ops: List[Tuple[str, int]], actor: Any) -> List[Tuple[str, int, int]]:
        key = str(chatid)
        results: List[Tuple[str, int, int]] = []
        with self._lock:
            data = self._boards.setdefault(key, {bg: 0 for bg in BG_ORDER})
            for bg, delta in ops:
                data[bg] = data.get(bg, 0) + delta
                results.append((bg, delta, data[bg]))
                self._log.append(
                    {
                        "chatid": key,
                        "bg": bg,
                        "delta": delta,
                        "by": str(actor or ""),
                        "ts": int(time.time()),
                    }
                )
            board = dict(data)
        self._save()
        print(f"计分 {key}: {[(bg, delta, new) for bg, delta, new in results]}")
        return [(bg, delta, board[bg]) for bg, delta, _ in results]

    def reset(self, chatid: Any, targets: Optional[List[str]] = None) -> Dict[str, int]:
        key = str(chatid)
        with self._lock:
            data = self._boards.setdefault(key, {bg: 0 for bg in BG_ORDER})
            for bg in targets or BG_ORDER:
                if bg in data:
                    data[bg] = 0
            board = dict(data)
        self._save()
        print(f"清零 {key}: {targets or BG_ORDER}")
        return board

    def handle(self, msg: Any) -> Optional[str]:
        """入口：记上下文、回完话再记一句机器人的回复。"""
        reply = self._dispatch(msg)
        if reply:
            self._llm.remember(str(msg.group_id), "机器人", reply)
        return reply

    def _dispatch(self, msg: Any) -> Optional[str]:
        """收到一条消息，返回要回复的 markdown；不需要回复时返回 None。"""
        chatid = str(msg.group_id)
        self.last_chatid = chatid
        text = ""
        for node in msg.chain or []:
            if isinstance(node, dict) and node.get("type") == "Plain":
                text = str(node.get("text") or "").strip()
                break

        sender = str(getattr(msg, "sender_id", "") or "")
        if text:
            self._llm.remember(chatid, sender, text)

        egg = self._eggs.record(chatid, sender, text)
        if egg:
            return "\n".join(egg)

        # 有 key 就先问 LLM，LLM 没把握（返回 None）再走正则规则。
        decision = self._llm.decide(chatid, sender, text)
        if decision:
            return self._apply_decision(chatid, decision, sender)

        board = self.board(chatid)
        if not text:
            return render_board(chatid, board, [random.choice(silent_lines())])

        if _HELP_RE.match(text):
            return HELP_TEXT

        # 带问号的当闲聊，别把「IEG今年加了多少分」当成加分指令。
        ops = [] if _ASK_RE.search(text) else parse_ops(text)
        if ops:
            results = self.apply(chatid, ops, getattr(msg, "sender_id", None))
            board_now = self.board(chatid)
            ranks = _ranks(board_now)
            header = [
                f"**{bg}** {_fmt(delta)} → **{_fmt(new)}** 分（第 {ranks[bg]} 名）"
                for bg, delta, new in results
            ]
            header.extend(flavor_lines(results, board_now))
            return render_board(chatid, board_now, header)

        if _RESET_RE.search(text):
            targets = [m.group(1).upper() for m in _BG_RE.finditer(text)]
            board = self.reset(chatid, targets or None)
            label = "、".join(targets) if targets else "全部"
            return render_board(chatid, board, [f"> 已清零：{label}"])

        if _QUERY_RE.search(text) or _BG_RE.search(text):
            return render_board(chatid, board)

        # 听不懂的话就不回，教程只在写了 help 的时候才出。
        return None

    def _apply_decision(self, chatid: str, decision: dict, actor: str) -> Optional[str]:
        """按 LLM 的判断出牌。"""
        action = decision.get("action")
        say = decision.get("say") or ""
        ops = [(op["bg"], int(op["delta"])) for op in decision.get("ops") or []]

        if action == "none":
            return None

        if action == "board":
            return render_board(chatid, self.board(chatid), [say] if say else None)

        if action == "reset":
            targets = [bg for bg, _ in ops]
            board = self.reset(chatid, targets or None)
            label = "、".join(targets) if targets else "全部"
            lines = [say] if say else []
            lines.append(f"> 已清零：{label}")
            return render_board(chatid, board, lines)

        results = self.apply(chatid, ops, actor)
        board_now = self.board(chatid)
        ranks = _ranks(board_now)
        header = [say] if say else []
        header.extend(
            f"**{bg}** {_fmt(delta)} → **{_fmt(new)}** 分（第 {ranks[bg]} 名）"
            for bg, delta, new in results
        )
        header.extend(flavor_lines(results, board_now))
        return render_board(chatid, board_now, header)
