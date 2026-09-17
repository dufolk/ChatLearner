"""Per-session Q&A word stock (pickle .cl files)."""

from __future__ import annotations

import copy
import json
import os
import pickle
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from message import chain_key, keep_text_and_image, parse_answer_text, strip_volatile

_lock = threading.Lock()


class WordStock:
    def __init__(self, root: str = "WordStock"):
        self.root = root
        os.makedirs(self.root, exist_ok=True)

    def _path(self, group: Any) -> str:
        return os.path.join(self.root, f"{group}.cl")

    def load(self, group: Any) -> Dict[str, Any]:
        path = self._path(group)
        with _lock:
            if not os.path.exists(path):
                return {}
            with open(path, "rb") as f:
                return pickle.load(f)

    def save(self, group: Any, data: Dict[str, Any]) -> None:
        path = self._path(group)
        with _lock:
            with open(path, "wb") as f:
                pickle.dump(data, f)

    def record_question(self, group: Any, chain: List[dict]) -> List[dict]:
        """Record question. Returns the original chain (for answer linking)."""
        raw = keep_text_and_image(chain)
        if not raw:
            return raw
        key = chain_key(raw)
        stock = self.load(group)
        if key not in stock:
            stock[key] = {
                "freq": 1,
                "time": int(time.time()),
                "answer": [],
                "regular": False,
            }
            print(time.strftime("%Y-%m-%d %H:%M:%S"), f"问题已记录 {group}.cl")
        else:
            stock[key]["freq"] = int(stock[key].get("freq", 0)) + 1
            print(time.strftime("%Y-%m-%d %H:%M:%S"), f"相同问题已累计 {group}.cl")
        self.save(group, stock)
        return raw

    def record_answer(
        self, group: Any, question_chain: List[dict], answer_chain: List[dict]
    ) -> None:
        q = keep_text_and_image(question_chain)
        a = keep_text_and_image(answer_chain)
        if not q or not a:
            return
        q_key = chain_key(q)
        stock = self.load(group)
        if q_key not in stock:
            return
        # Prefer JSON for new writes; lookup still accepts legacy str(list).
        answer_payload = json.dumps(a, ensure_ascii=False)
        answer_cmp = strip_volatile(a)
        entry = {
            "answertext": answer_payload,
            "time": datetime.now().isoformat(timespec="seconds"),
            "same": 1,
        }
        answers = stock[q_key].setdefault("answer", [])
        for old in answers:
            old_chain = parse_answer_text(old.get("answertext"))
            if strip_volatile(old_chain) == answer_cmp:
                old["same"] = int(old.get("same", 0)) + 1
                old["time"] = entry["time"]
                # Refresh stored payload to keep a usable image url when present.
                old["answertext"] = answer_payload
                self.save(group, stock)
                print(time.strftime("%Y-%m-%d %H:%M:%S"), f"答案重复已累计 {group}.cl")
                return
        answers.append(entry)
        self.save(group, stock)
        print(time.strftime("%Y-%m-%d %H:%M:%S"), f"答案已记录 {group}.cl")

    def get_answers(self, group: Any, question_chain: List[dict]) -> Optional[List[dict]]:
        """Exact-match lookup. Returns answer entry list or None."""
        q = keep_text_and_image(question_chain)
        if not q:
            return None
        stock = self.load(group)
        key = chain_key(q)
        node = stock.get(key)
        if not node:
            return None
        answers = node.get("answer") or []
        return copy.deepcopy(answers) if answers else None
