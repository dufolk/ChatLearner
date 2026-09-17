"""BG 积分 bot 入口：企微长连接收发 + 计分。"""

from __future__ import annotations

import sys
import threading
import time

from scorebot import ScoreBot, render_board
from wecom_adapter import WeComAdapter

VERSION = "1.0.0"
_POLL_INTERVAL = 0.5


def _help() -> None:
    print(
        """
控制台指令:
  help       控制台帮助
  board      打印最近一个会话的积分榜
  peek       只收不发的探测窗口（确认有没有收到 @机器人的消息）
  exit       退出
""".strip()
    )


def _poll_loop(adapter: WeComAdapter, bot: ScoreBot, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            for msg in adapter.fetch_messages():
                try:
                    reply = bot.handle(msg)
                except Exception as e:
                    print("处理消息异常:", e)
                    continue
                if not reply:
                    continue
                try:
                    adapter.send_chain(
                        msg.group_id,
                        [{"type": "Plain", "text": reply}],
                        is_group=msg.is_group,
                    )
                except Exception as e:
                    print("回复失败:", e)
        except Exception as e:
            print("拉取消息异常:", e)
        time.sleep(_POLL_INTERVAL)


def main() -> None:
    # Windows 控制台默认 GBK，打印奖牌 emoji 会炸
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"BG 积分 bot {VERSION} — IEG / CDG / TEG / CSIG / WXG / PCG")
    bot = ScoreBot()
    adapter = WeComAdapter()
    adapter.connect()

    stop = threading.Event()
    threading.Thread(target=_poll_loop, args=(adapter, bot, stop), daemon=True).start()
    print("已就绪：在群里 @机器人 发「IEG加1分」试试。输入 help 看控制台指令。")

    while True:
        try:
            line = input("> ").strip()
        except KeyboardInterrupt:
            line = "exit"
        except EOFError:
            # 后台运行没有 stdin，别退出，挂着等被杀
            print("无控制台输入（后台运行），保持长连接…")
            while True:
                time.sleep(3600)
        if not line:
            continue
        cmd = line.split()[0].lower()
        if cmd in ("help", "?"):
            _help()
        elif cmd == "board":
            if not bot.last_chatid:
                print("还没有收到过消息")
            else:
                print(render_board(bot.last_chatid, bot.board(bot.last_chatid)))
        elif cmd == "peek":
            adapter.peek_inbound(seconds=10.0)
        elif cmd == "exit":
            stop.set()
            adapter.close()
            print("退出中…")
            break
        else:
            print(f'未知指令 "{line}"，输入 help 查看')


if __name__ == "__main__":
    main()
