"""WeCom (企业微信) 智能机器人长连接适配器。

wss://openws.work.weixin.qq.com
  aibot_subscribe     认证（BotID + Secret）
  aibot_msg_callback  收消息
  aibot_respond_msg   被动回复（带回调 req_id）
  aibot_send_msg      主动推送
  ping                心跳

只处理文本：收进来的消息转成 [{"type": "Plain", "text": ...}]，
发出去的内容按 markdown 渲染。
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import threading
import time
import uuid
from typing import Any, List, Optional

from adapter import BotAdapter, IncomingMessage
from configutil import load_dotenv_file

WS_URL_DEFAULT = "wss://openws.work.weixin.qq.com"
AT_MENTION = re.compile(r"@\S+\s*")


def _req_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class _WeComLoop:
    def __init__(self, bot_id: str, secret: str, ws_url: str):
        self.bot_id = bot_id
        self.secret = secret
        self.ws_url = ws_url
        self.incoming: "queue.Queue[IncomingMessage]" = queue.Queue()
        self.seen_chats: dict = {}
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._auth_ok = threading.Event()
        self._auth_error: Optional[str] = None
        self._pending: dict = {}
        self._reply_ctx: dict = {}  # chatid -> 最近一次回调的 req_id
        self._stop = threading.Event()

    def start(self, timeout: float = 20.0) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("企微 WebSocket 线程未启动")
        if not self._auth_ok.wait(timeout):
            raise TimeoutError(self._auth_error or "企微认证超时")
        if self._auth_error:
            raise RuntimeError(self._auth_error)

    def stop(self) -> None:
        self._stop.set()
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop)

    def call(self, coro, timeout: float = 30.0):
        if not self._loop:
            raise RuntimeError("企微未连接")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_until_complete(self._main())
        finally:
            self._loop.close()

    async def _main(self) -> None:
        import websockets

        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    await self._subscribe()
                    tasks = {
                        asyncio.create_task(self._recv_loop()),
                        asyncio.create_task(self._heartbeat()),
                        asyncio.create_task(self._wait_stop()),
                    }
                    done, pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for t in pending:
                        t.cancel()
                    backoff = 1.0
            except Exception as e:
                if self._stop.is_set():
                    break
                print("企微连接异常，重连:", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                self._ws = None

    async def _wait_stop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(0.2)

    async def _close_ws(self) -> None:
        if self._ws is not None:
            await self._ws.close()

    async def _subscribe(self) -> None:
        await self._send(
            {
                "cmd": "aibot_subscribe",
                "headers": {"req_id": _req_id("aibot_subscribe")},
                "body": {"bot_id": self.bot_id, "secret": self.secret},
            }
        )

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(30)
            await self._send({"cmd": "ping", "headers": {"req_id": _req_id("ping")}})

    async def _recv_loop(self) -> None:
        async for raw in self._ws:
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await self._handle(frame)

    async def _handle(self, frame: dict) -> None:
        cmd = frame.get("cmd") or ""
        req_id = (frame.get("headers") or {}).get("req_id") or ""

        if cmd == "aibot_msg_callback":
            self._on_message(frame)
            return
        if cmd == "aibot_event_callback":
            event = ((frame.get("body") or {}).get("event") or {}).get("eventtype")
            if event == "disconnected_event":
                print("企微：新连接顶替了当前长连接")
            return

        if req_id.startswith("aibot_subscribe"):
            if frame.get("errcode", -1) != 0:
                self._auth_error = (
                    f"认证失败 errcode={frame.get('errcode')} errmsg={frame.get('errmsg')}"
                )
            else:
                self._auth_error = None
                print("企微长连接已认证")
            self._auth_ok.set()
            return

        if req_id in self._pending:
            fut = self._pending.pop(req_id)
            if not fut.done():
                fut.set_result(frame)

    def _on_message(self, frame: dict) -> None:
        body = frame.get("body") or {}
        chattype = body.get("chattype") or "single"
        chatid = body.get("chatid") or (body.get("from") or {}).get("userid")
        sender = (body.get("from") or {}).get("userid")
        msgid = body.get("msgid") or uuid.uuid4().hex
        req_id = (frame.get("headers") or {}).get("req_id")
        ts = int(body.get("create_time") or time.time())
        if chatid and req_id:
            self._reply_ctx[str(chatid)] = req_id
        if chatid:
            self.seen_chats[str(chatid)] = {
                "chatid": chatid,
                "chattype": chattype,
                "sender": sender,
            }

        text = self._extract_text(body)
        if not text:
            return
        self.incoming.put(
            IncomingMessage(
                group_id=chatid,
                sender_id=sender,
                message_id=msgid,
                timestamp=ts,
                chain=[{"type": "Plain", "text": text}],
                raw=frame,
                is_group=(chattype == "group"),
            )
        )
        print(f"企微收消息 chattype={chattype} chatid={chatid} from={sender}: {text}")

    def _extract_text(self, body: dict) -> str:
        """群聊/单聊文本原文，去掉 @xxx 前缀；图文混排里的字也一并拼上。"""
        msgtype = body.get("msgtype")
        parts: List[str] = []
        if msgtype == "text":
            parts.append((body.get("text") or {}).get("content") or "")
        elif msgtype == "mixed":
            for item in (body.get("mixed") or {}).get("msg_item") or []:
                if item.get("msgtype") == "text":
                    parts.append((item.get("text") or {}).get("content") or "")
        quote = body.get("quote") or {}
        if quote.get("msgtype") == "text":
            parts.append((quote.get("text") or {}).get("content") or "")
        text = AT_MENTION.sub("", " ".join(parts)).strip()
        return text

    async def _send(self, frame: dict) -> None:
        if self._ws is None:
            raise ConnectionError("企微未连接")
        await self._ws.send(json.dumps(frame, ensure_ascii=False))

    async def send_and_wait(
        self, cmd: str, body: dict, timeout: float = 15.0, req_id: Optional[str] = None
    ) -> dict:
        rid = req_id or _req_id(cmd)
        fut = self._loop.create_future()
        self._pending[rid] = fut
        await self._send({"cmd": cmd, "headers": {"req_id": rid}, "body": body})
        return await asyncio.wait_for(fut, timeout=timeout)

    async def send_chain(self, target: Any, chain: List[dict], is_group: bool) -> Any:
        chatid = str(target)
        text = "\n".join(
            str(n.get("text", "")) for n in chain if n.get("type") == "Plain"
        ).strip()
        if not text:
            return None
        body = {"msgtype": "markdown", "markdown": {"content": text}}
        req_id = self._reply_ctx.get(chatid)
        if req_id:
            ack = await self.send_and_wait("aibot_respond_msg", body, req_id=req_id)
            if ack.get("errcode", -1) == 0:
                return ack
            print(f"respond_msg 失败({ack.get('errmsg')})，回退 send_msg")
        payload = dict(body)
        payload["chatid"] = chatid
        payload["chat_type"] = 2 if is_group else 1
        return await self.send_and_wait("aibot_send_msg", payload)


class WeComAdapter(BotAdapter):
    def __init__(self):
        load_dotenv_file()
        self.bot_id = os.getenv("WECOM_BOT_ID", "").strip()
        self.secret = os.getenv("WECOM_SECRET", "").strip()
        self.ws_url = os.getenv("WECOM_WS_URL", WS_URL_DEFAULT).strip() or WS_URL_DEFAULT
        self._client: Optional[_WeComLoop] = None

    def connect(self) -> None:
        if not self.bot_id or not self.secret:
            raise RuntimeError("缺少 WECOM_BOT_ID / WECOM_SECRET（写在 .env）")
        self._client = _WeComLoop(self.bot_id, self.secret, self.ws_url)
        self._client.start()

    def fetch_messages(self) -> List[IncomingMessage]:
        if not self._client:
            return []
        out = []
        while True:
            try:
                out.append(self._client.incoming.get_nowait())
            except queue.Empty:
                break
        return out

    def send_chain(
        self, target: Any, chain: List[dict], *, is_group: bool = True
    ) -> Optional[Any]:
        if not self._client:
            raise RuntimeError("企微未连接")
        return self._client.call(self._client.send_chain(target, chain, is_group))

    def peek_inbound(self, seconds: float = 10.0) -> None:
        """只收不发的探测窗口，用来确认有没有收到 @机器人的消息、拿到 chatid。"""
        if not self._client:
            return
        print(f"接收探测 {seconds:.0f}s（不发送）：现在去群里 @机器人 发一句话")
        deadline = time.time() + seconds
        hits = []
        while time.time() < deadline:
            try:
                hits.append(self._client.incoming.get(timeout=0.4))
            except queue.Empty:
                continue
        for msg in hits:
            self._client.incoming.put(msg)
        chats = list(self._client.seen_chats.values())
        print(f"探测结束：收到 {len(hits)} 条，已知会话 {len(chats)} 个")
        for c in chats:
            print("  ", c)
        if not chats:
            print("没收到任何消息：确认机器人在这个群里、并且这条消息 @ 了它。")

    def close(self) -> None:
        if self._client:
            self._client.stop()
