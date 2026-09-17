"""WeCom (企业微信) AI Bot adapter via WebSocket long connection.

Mirrors stock-assistant's aibot push path (chatid + markdown/media),
using the official gateway instead of wecom-cli:

  wss://openws.work.weixin.qq.com
  aibot_subscribe / aibot_msg_callback / aibot_respond_msg / aibot_send_msg

Credentials: WECOM_BOT_ID + WECOM_SECRET in .env (never commit).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import queue
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import requests

from adapter import BotAdapter, IncomingMessage
from configutil import load_dotenv_file

WS_URL_DEFAULT = "wss://openws.work.weixin.qq.com"
AT_MENTION = re.compile(r"@\S+\s*")


def _req_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _decrypt_aes(data: bytes, aeskey: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = base64.b64decode(aeskey)
    if len(key) != 32:
        raise ValueError(f"aeskey decoded length {len(key)}, expected 32")
    iv = key[:16]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    pad = padded[-1]
    if 1 <= pad <= 32:
        return padded[:-pad]
    return padded


def _download_media(url: str, aeskey: Optional[str]) -> Optional[bytes]:
    try:
        raw = requests.get(url, timeout=20).content
    except Exception as e:
        print("下载媒体失败:", e)
        return None
    if not aeskey:
        return raw
    try:
        return _decrypt_aes(raw, aeskey)
    except Exception as e:
        print("媒体解密失败:", e)
        return raw


class _WeComLoop:
    def __init__(self, bot_id: str, secret: str, ws_url: str):
        self.bot_id = bot_id
        self.secret = secret
        self.ws_url = ws_url
        self.incoming: "queue.Queue[IncomingMessage]" = queue.Queue()
        self.seen_chats: Dict[str, dict] = {}
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._auth_ok = threading.Event()
        self._auth_error: Optional[str] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._reply_ctx: Dict[str, str] = {}  # chatid -> last req_id
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
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

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
                    max_size=20 * 1024 * 1024,
                ) as ws:
                    self._ws = ws
                    await self._subscribe()
                    recv = asyncio.create_task(self._recv_loop())
                    hb = asyncio.create_task(self._heartbeat())
                    stopper = asyncio.create_task(self._wait_stop())
                    done, pending = await asyncio.wait(
                        {recv, hb, stopper},
                        return_when=asyncio.FIRST_COMPLETED,
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
            await self._send(
                {"cmd": "ping", "headers": {"req_id": _req_id("ping")}}
            )

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
            err = frame.get("errcode", -1)
            if err != 0:
                self._auth_error = (
                    f"认证失败 errcode={err} errmsg={frame.get('errmsg')}"
                )
                self._auth_ok.set()
            else:
                self._auth_error = None
                self._auth_ok.set()
                print("企微长连接已认证")
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
        info = {
            "chatid": chatid,
            "chattype": chattype,
            "msgtype": body.get("msgtype"),
            "sender": sender,
            "has_quote": bool(body.get("quote")),
        }
        if chatid:
            self.seen_chats[str(chatid)] = info
        chain = self._to_chain(body)
        if not chain:
            return
        self.incoming.put(
            IncomingMessage(
                group_id=chatid,
                sender_id=sender,
                message_id=msgid,
                timestamp=ts,
                chain=chain,
                raw=frame,
                is_group=(chattype == "group"),
            )
        )
        print(
            f"企微收消息 chattype={chattype} chatid={chatid} "
            f"from={sender} msgtype={body.get('msgtype')} "
            f"quote={bool(body.get('quote'))}"
        )

    def _to_chain(self, body: dict) -> List[dict]:
        chain: List[dict] = self._to_nodes(body.get("msgtype"), body)
        # 群里 @机器人 + 引用别人消息时，被引用原文在 quote 里。
        # 官方通道只推送 @机器人的消息，引用是拿到群里其他内容的唯一途径。
        quote = body.get("quote") or {}
        if quote:
            for node in self._to_nodes(quote.get("msgtype"), quote):
                if node not in chain:
                    chain.append(node)
        return chain

    def _to_nodes(self, msgtype: Optional[str], node: dict) -> List[dict]:
        chain: List[dict] = []
        if msgtype == "text":
            text = AT_MENTION.sub("", (node.get("text") or {}).get("content") or "").strip()
            if text:
                chain.append({"type": "Plain", "text": text})
        elif msgtype == "image":
            img = self._image_node(node.get("image") or {})
            if img:
                chain.append(img)
        elif msgtype == "mixed":
            for item in (node.get("mixed") or {}).get("msg_item") or []:
                chain.extend(self._to_nodes(item.get("msgtype"), item))
        return chain

    def _image_node(self, image: dict) -> Optional[dict]:
        url = image.get("url")
        if not url:
            return None
        node = {"type": "Image", "url": url}
        if image.get("aeskey"):
            node["aeskey"] = image["aeskey"]
        data = _download_media(url, image.get("aeskey"))
        if data:
            node["base64"] = base64.b64encode(data).decode("ascii")
        return node

    async def _send(self, frame: dict) -> None:
        if self._ws is None:
            raise ConnectionError("企微未连接")
        await self._ws.send(json.dumps(frame, ensure_ascii=False))

    async def send_and_wait(
        self,
        cmd: str,
        body: dict,
        timeout: float = 15.0,
        req_id: Optional[str] = None,
    ) -> dict:
        rid = req_id or _req_id(cmd)
        fut = self._loop.create_future()
        self._pending[rid] = fut
        await self._send({"cmd": cmd, "headers": {"req_id": rid}, "body": body})
        return await asyncio.wait_for(fut, timeout=timeout)

    async def send_chain(self, target: Any, chain: List[dict], is_group: bool) -> Any:
        chatid = str(target)
        texts = [n.get("text", "") for n in chain if n.get("type") == "Plain"]
        images = [n for n in chain if n.get("type") == "Image"]
        last_id = None
        if texts:
            last_id = await self._send_text(chatid, "\n".join(texts), is_group)
        for img in images:
            last_id = await self._send_image(chatid, img, is_group)
        return last_id

    async def _deliver(self, chatid: str, body: dict, is_group: bool) -> Any:
        """优先用回调里的 req_id 被动回复，失败则退化为主动推送。"""
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

    async def _send_text(self, chatid: str, text: str, is_group: bool) -> Any:
        body = {
            "msgtype": "markdown",
            "markdown": {"content": text},
        }
        return await self._deliver(chatid, body, is_group)

    async def _send_image(self, chatid: str, node: dict, is_group: bool) -> Any:
        data = None
        if node.get("base64"):
            data = base64.b64decode(node["base64"])
        elif node.get("path") and os.path.exists(node["path"]):
            with open(node["path"], "rb") as f:
                data = f.read()
        elif node.get("url"):
            data = _download_media(node["url"], node.get("aeskey"))
        if not data:
            raise RuntimeError("图片无可用数据")
        media_id = await self._upload_media(data, "image", "image.jpg")
        media_body = {"msgtype": "image", "image": {"media_id": media_id}}
        return await self._deliver(chatid, media_body, is_group)

    async def _upload_media(self, data: bytes, typ: str, filename: str) -> str:
        chunk = 512 * 1024
        total = max(1, (len(data) + chunk - 1) // chunk)
        init = await self.send_and_wait(
            "aibot_upload_media_init",
            {
                "type": typ,
                "filename": filename,
                "total_size": len(data),
                "total_chunks": total,
                "md5": hashlib.md5(data).hexdigest(),
            },
        )
        if init.get("errcode", 0) != 0:
            raise RuntimeError(f"upload init failed: {init}")
        upload_id = (init.get("body") or {}).get("upload_id")
        if not upload_id:
            raise RuntimeError(f"no upload_id: {init}")
        for i in range(total):
            part = data[i * chunk : (i + 1) * chunk]
            ack = await self.send_and_wait(
                "aibot_upload_media_chunk",
                {
                    "upload_id": upload_id,
                    "chunk_index": i,
                    "base64_data": base64.b64encode(part).decode("ascii"),
                },
            )
            if ack.get("errcode", 0) != 0:
                raise RuntimeError(f"chunk {i} failed: {ack}")
        finish = await self.send_and_wait(
            "aibot_upload_media_finish", {"upload_id": upload_id}
        )
        media_id = (finish.get("body") or {}).get("media_id")
        if not media_id:
            raise RuntimeError(f"upload finish failed: {finish}")
        return media_id


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
        self,
        target: Any,
        chain: List[dict],
        *,
        is_group: bool = True,
    ) -> Optional[Any]:
        # 配了群机器人 webhook 就优先用它：不用 @，也不要求会话先被 @ 过。
        from webhook import resolve_key, send_chain as webhook_send

        key = resolve_key(target)
        if key:
            return webhook_send(key, chain)
        if not self._client:
            raise RuntimeError("企微未连接")
        return self._client.call(self._client.send_chain(target, chain, is_group))

    def peek_inbound(self, seconds: float = 8.0, name_hint: str = "6组！") -> None:
        """Receive-only window. Does not send."""
        if not self._client:
            return
        print(f"接收探测 {seconds:.0f}s（不发送）。目标群名提示: {name_hint!r}")
        print(
            "说明：回调只给 chatid / chattype / from.userid，不带群名；"
            "群聊里只有 @机器人 的消息才会推送。现在去群里 @机器人 发一条试试。"
        )
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
            print(
                f"这 {seconds:.0f}s 内没有任何会话。确认机器人已被拉进「{name_hint}」，"
                "并在群里 @它 发一条——控制台会打出 chatid，再 add both <chatid>。"
            )
        else:
            print("若其中一个就是目标群：add both <chatid>，或填进 config.json 的 group_aliases。")

    def close(self) -> None:
        if self._client:
            self._client.stop()
