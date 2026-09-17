"""
ChatLearner — minimal learned chatbot (text + image only).

Core is platform-agnostic. Wire in a BotAdapter for your chat platform.
"""

from __future__ import annotations

import os
import time

from configutil import load_config, save_config, load_dotenv_file
from engine import Engine
from msgaudit_adapter import MsgAuditAdapter
from wecom_adapter import WeComAdapter
from wordstock import WordStock

VERSION = "4.0.0-minimal"


def _help() -> None:
    print(
        """
指令:
  help                         帮助
  learning                     开/关学习
  reply                        开/关回复
  add learning <会话ID...>     加入学习会话
  remove learning <会话ID...>  移除学习会话
  add reply <会话ID...>        加入回复会话
  remove reply <会话ID...>     移除回复会话
  add both <会话ID...>         同时加入学习与回复
  interval <秒>                词库链间隔（默认900）
  replychance <0-100>          回复概率
  grouplist                    查看会话列表
  rooms                        存档模式：列出见过的 roomid（存档不返回群名）
  bind <名字> <roomid>         把群名绑定到存档 roomid
  hook <名字|会话ID> <key>     绑定群机器人 webhook key
  peek                         只收不发，看近期会话（可用来确认 6组！）
  status                       查看状态
  exit                         退出
""".strip()
    )


def _parse_ids(parts) -> list:
    ids = []
    for p in parts:
        try:
            ids.append(int(p))
        except ValueError:
            ids.append(p)
    return ids


def _add_list(cfg: dict, key: str, ids: list) -> None:
    lst = list(cfg.get(key) or [])
    for i in ids:
        if i not in lst:
            lst.append(i)
    cfg[key] = lst
    save_config(cfg)
    print(key, cfg[key])


def _remove_list(cfg: dict, key: str, ids: list) -> None:
    lst = [x for x in (cfg.get(key) or []) if x not in ids]
    cfg[key] = lst
    save_config(cfg)
    print(key, cfg[key])


def main() -> None:
    print(f"ChatLearner {VERSION} — 企微文本/图片学习与回复")
    cfg = load_config()
    cfg["stopsign"] = 0
    cfg["learning"] = 0
    cfg["reply"] = 0
    save_config(cfg)

    load_dotenv_file()
    # 存档模式：配了 WECOM_MSGAUDIT_CORPID 就走存档，群里不 @ 也能收到全部消息。
    use_audit = bool(os.getenv("WECOM_MSGAUDIT_CORPID", "").strip())
    adapter = MsgAuditAdapter() if use_audit else WeComAdapter()
    adapter.connect()

    stock = WordStock()
    engine = Engine(adapter, stock)
    engine.start()
    _help()

    while True:
        try:
            line = input("ChatLearner> ").strip()
        except (EOFError, KeyboardInterrupt):
            line = "exit"
        if not line:
            continue
        parts = line.split()
        cmd = parts[0].lower()
        cfg = load_config()

        if cmd in ("help", "?"):
            _help()
        elif cmd == "learning":
            cfg["learning"] = 0 if int(cfg.get("learning", 0)) == 1 else 1
            cfg["stopsign"] = 0
            save_config(cfg)
            print("learning =", cfg["learning"])
        elif cmd == "reply":
            cfg["reply"] = 0 if int(cfg.get("reply", 0)) == 1 else 1
            cfg["stopsign"] = 0
            save_config(cfg)
            print("reply =", cfg["reply"])
        elif cmd == "add" and len(parts) >= 3:
            kind = parts[1].lower()
            ids = _parse_ids(parts[2:])
            if kind == "learning":
                _add_list(cfg, "learninggrouplist", ids)
            elif kind == "reply":
                _add_list(cfg, "replygrouplist", ids)
            elif kind in ("both", "learnings"):
                _add_list(cfg, "learninggrouplist", ids)
                cfg = load_config()
                _add_list(cfg, "replygrouplist", ids)
            else:
                print("未知 add 类型")
        elif cmd == "remove" and len(parts) >= 3:
            kind = parts[1].lower()
            ids = _parse_ids(parts[2:])
            if kind == "learning":
                _remove_list(cfg, "learninggrouplist", ids)
            elif kind == "reply":
                _remove_list(cfg, "replygrouplist", ids)
            else:
                print("未知 remove 类型")
        elif cmd == "interval" and len(parts) == 2:
            cfg["interval"] = int(parts[1])
            save_config(cfg)
            print("interval =", cfg["interval"])
        elif cmd == "replychance" and len(parts) == 2:
            cfg["replychance"] = int(parts[1])
            save_config(cfg)
            print("replychance =", cfg["replychance"])
        elif cmd == "grouplist":
            print("学习会话:", cfg.get("learninggrouplist"))
            print("回复会话:", cfg.get("replygrouplist"))
            print("群名映射:", cfg.get("group_aliases"))
        elif cmd == "rooms":
            if hasattr(adapter, "dump_rooms"):
                rooms = adapter.dump_rooms()
                print(f"已见群 {len(rooms)} 个（存档不返回群名，用内容/成员认）")
                for rid, info in rooms.items():
                    print("  ", rid, info)
                if not rooms:
                    print("还没有消息。确认群里有人发言且成员在存档授权范围内。")
            else:
                print("当前不是存档模式，用 peek。")
        elif cmd == "bind" and len(parts) == 3:
            cfg["group_aliases"][parts[1]] = parts[2]
            save_config(cfg)
            print("group_aliases", cfg["group_aliases"])
        elif cmd == "hook" and len(parts) == 3:
            cfg.setdefault("webhook_keys", {})[parts[1]] = parts[2]
            save_config(cfg)
            print("webhook_keys", cfg["webhook_keys"])
        elif cmd == "peek":
            adapter.peek_inbound(seconds=8.0, name_hint="6组！")
        elif cmd == "status":
            print(
                f"learning={cfg.get('learning')} reply={cfg.get('reply')} "
                f"interval={cfg.get('interval')} replychance={cfg.get('replychance')}"
            )
        elif cmd == "exit":
            cfg["stopsign"] = 1
            cfg["learning"] = 0
            cfg["reply"] = 0
            save_config(cfg)
            engine.stop()
            adapter.close()
            print("退出中…")
            time.sleep(0.8)
            break
        else:
            print(f'未知指令 "{line}"，输入 help 查看')


if __name__ == "__main__":
    main()
