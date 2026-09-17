"""彩蛋：刷屏、复读、深夜刷分之类的不正经回复。

两类：
  - 拦截型（刷屏 / 复读 / 纯起哄）：直接回彩蛋，不跑计分逻辑；
  - 风味型（深夜 / 端水 / 反复横跳 / 特殊数字）：作为备注行贴在积分榜上面。
"""

from __future__ import annotations

import random
import time
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional, Tuple

SPAM_WINDOW = 12.0  # 秒：两次 @ 间隔超过这个就不算连发
SPAM_COUNT = 3
REPEAT_COUNT = 3
WATER_COUNT = 4  # 一条消息里动几个 BG 算端水
QUIET_FROM, QUIET_TO = 0, 5  # 深夜区间 [0,5)

CHEER_WORDS = {"666", "6", "66", "yyds", "nb", "牛", "牛逼", "绝了", "牛啊"}
FUN_SCORES = {
    66: "{bg} 66 大顺",
    99: "{bg} 99，差一分圆满",
    100: "{bg} 满分猎人出现了",
    114: "{bg} 114514……",
    520: "{bg} 520，这里不是表白群",
    250: "{bg} 这个数字听起来不太礼貌",
    -250: "{bg} 扣分扣得很有灵性",
}


def spam_lines(count: int) -> List[str]:
    return ["**食不食油饼**", f"> 连着 @ 我 {count} 次了，先歇会儿。"]


def repeat_lines(text: str) -> List[str]:
    return [
        f"**复读机 ECO Mode**",
        f"> 「{text}」你说第 {REPEAT_COUNT} 遍了，我记账还是你记账？",
    ]


def cheer_lines() -> List[str]:
    return ["**666**", f"> 收到夸奖，但夸奖不计分。"]


def silent_lines() -> List[str]:
    return [
        "> 光 @ 不说话，是有什么心事吗",
        "> 嗯？",
        "> 你 @ 了我，但什么都没说。我替你尴尬了一下。",
    ]


def quiet_lines() -> List[str]:
    return [
        "> 这个点还在刷分，是真爱。",
        "> 凌晨了，积分不会跑，你先去睡。",
    ]


class EggBox:
    """按「会话 + 人」记录最近几句话，用来识别刷屏和复读。"""

    def __init__(self) -> None:
        self._history: Dict[Tuple[str, str], deque] = {}

    def record(self, chatid: str, sender: str, text: str) -> Optional[List[str]]:
        key = (str(chatid), str(sender))
        now = time.time()
        history = self._history.setdefault(key, deque(maxlen=8))
        while history and now - history[0][0] > SPAM_WINDOW:
            history.popleft()
        history.append((now, text))

        if len(history) >= SPAM_COUNT:
            count = len(history)
            history.clear()
            return spam_lines(count)

        if len(history) >= REPEAT_COUNT:
            tail = [t for _, t in history][-REPEAT_COUNT:]
            if tail[0].strip() and len(set(tail)) == 1:
                history.clear()
                return repeat_lines(tail[0].strip())

        if text.strip().lower() in CHEER_WORDS:
            history.clear()
            return cheer_lines()
        return None


def flavor_lines(
    results: List[Tuple[str, int, int]], board: Dict[str, int]
) -> List[str]:
    """贴在积分榜上面的吐槽行。"""
    lines: List[str] = []

    hour = datetime.now().hour
    if QUIET_FROM <= hour < QUIET_TO:
        lines.append(random.choice(quiet_lines()))

    changed = {bg for bg, _, _ in results}
    if len(changed) >= WATER_COUNT:
        lines.append(f"> 端水大师：一口气动了 {len(changed)} 个 BG。")

    signs: Dict[str, set] = {}
    for bg, delta, _ in results:
        signs.setdefault(bg, set()).add(1 if delta > 0 else -1)
    swings = [bg for bg, group in signs.items() if len(group) > 1]
    if swings:
        lines.append(f"> {'、'.join(swings)} 反复横跳.jpg")

    for bg, _, new in results:
        note = FUN_SCORES.get(new)
        if note:
            lines.append(f"> {note.format(bg=bg)}")

    if len(results) >= 2 and all(delta > 0 for _, delta, _ in results):
        lines.append("> 全是加分，今天的和睦由你守护。")
    if len(results) >= 2 and all(delta < 0 for _, delta, _ in results):
        lines.append("> 全是扣分，有点狠。")

    return lines
