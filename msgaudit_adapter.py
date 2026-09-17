"""企业微信「会话内容存档」接收适配器。

群里不用 @ 机器人也能拿到全部聊天记录（前提：企业开通了存档、目标群有成员在授权范围内）。

这套 API 不走 HTTP，必须加载官方原生 SDK：
  Windows: WeWorkFinanceSdk.dll
  Linux:   libWeWorkFinanceSdk.so
下载：开放文档中心 -> 会话内容存档 -> SDK

流程：
  Init(corpid, secret) -> GetChatData(seq, limit)
  -> 每条消息：encrypt_random_key 经 base64 decode + RSA(PKCS1) 私钥解密
  -> 连同 encrypt_chat_msg 交给 DecryptData 得到明文 JSON
  -> 明文里 roomid 是群 id（存档不返回群名），msgtime 毫秒，msgtype text/image/mixed

.env 需要的变量见 .env.example。
注意：只保留最近 5 天；本适配器只做文本 + 图片占位，不下载媒体。
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import queue
import threading
import time
from typing import Any, Dict, List, Optional

from adapter import BotAdapter, IncomingMessage
from configutil import load_dotenv_file

SEQ_FILE = "msgaudit_seq.json"
POLL_INTERVAL = 5.0
PULL_LIMIT = 1000
MAX_QUEUE = 5000

_DLL_CANDIDATES = [
    "WeWorkFinanceSdk.dll",
    "libWeWorkFinanceSdk.so",
    os.path.join("sdk", "WeWorkFinanceSdk.dll"),
    os.path.join("sdk", "libWeWorkFinanceSdk.so"),
]


def _load_private_key(path: str):
    from cryptography.hazmat.primitives import serialization

    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def _find_dll() -> str:
    env = os.getenv("WECOM_SDK_DLL", "").strip()
    candidates = [env] if env else _DLL_CANDIDATES
    for path in candidates:
        if path and os.path.exists(path):
            return path
    raise FileNotFoundError(
        "找不到 WeComFinanceSdk，把官方 SDK 放到项目根目录/sdk 下，"
        "或在 .env 里用 WECOM_SDK_DLL 指定完整路径"
    )


class _WeComFinanceSdk:
    """官方 SDK 的 ctypes 封装。"""

    def __init__(self, dll_path: str):
        self.dll = ctypes.CDLL(dll_path)
        d = self.dll
        d.NewSdk.restype = ctypes.c_void_p
        d.NewSdk.argtypes = []
        d.DestroySdk.argtypes = [ctypes.c_void_p]
        d.Init.restype = ctypes.c_int
        d.Init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        d.NewSlice.restype = ctypes.c_void_p
        d.NewSlice.argtypes = []
        d.FreeSlice.argtypes = [ctypes.c_void_p]
        d.GetContentFromSlice.restype = ctypes.c_char_p
        d.GetContentFromSlice.argtypes = [ctypes.c_void_p]
        d.GetChatData.restype = ctypes.c_int
        d.GetChatData.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_uint,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        d.DecryptData.restype = ctypes.c_int
        d.DecryptData.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p]
        self.sdk = d.NewSdk()

    def init(self, corpid: str, secret: str) -> None:
        ret = self.dll.Init(
            self.sdk, corpid.encode("utf-8"), secret.encode("utf-8")
        )
        if ret != 0:
            raise RuntimeError(f"SDK Init 失败 ret={ret}")

    def get_chat_data(self, seq: int, limit: int = PULL_LIMIT) -> dict:
        slice_ = self.dll.NewSlice()
        try:
            ret = self.dll.GetChatData(
                self.sdk, ctypes.c_uint64(seq), ctypes.c_uint(limit),
                None, None, 20, slice_,
            )
            if ret != 0:
                raise RuntimeError(f"GetChatData 失败 ret={ret}")
            raw = self.dll.GetContentFromSlice(slice_)
            payload = json.loads((raw or b"{}").decode("utf-8"))
            if payload.get("errcode", 0) != 0:
                raise RuntimeError(f"GetChatData errcode={payload.get('errcode')} "
                                   f"errmsg={payload.get('errmsg')}")
            return payload
        finally:
            self.dll.FreeSlice(slice_)

    def decrypt(self, random_key: bytes, encrypt_msg: str) -> dict:
        slice_ = self.dll.NewSlice()
        try:
            ret = self.dll.DecryptData(
                random_key, encrypt_msg.encode("utf-8"), slice_
            )
            if ret != 0:
                raise RuntimeError(f"DecryptData 失败 ret={ret}")
            raw = self.dll.GetContentFromSlice(slice_)
            return json.loads((raw or b"{}").decode("utf-8"))
        finally:
            self.dll.FreeSlice(slice_)

    def close(self) -> None:
        try:
            self.dll.DestroySdk(self.sdk)
        except Exception:
            pass


class MsgAuditAdapter(BotAdapter):
    def __init__(self):
        load_dotenv_file()
        self.corpid = os.getenv("WECOM_MSGAUDIT_CORPID", "").strip()
        self.secret = os.getenv("WECOM_MSGAUDIT_SECRET", "").strip()
        self.key_path = os.getenv("WECOM_MSGAUDIT_PRIVATE_KEY", "").strip()
        self._sdk: Optional[_WeComFinanceSdk] = None
        self._privkey = None
        self._seq = 0
        self.incoming: "queue.Queue[IncomingMessage]" = queue.Queue(maxsize=MAX_QUEUE)
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self.seen_rooms: Dict[str, dict] = {}

    def connect(self) -> None:
        missing = [
            name
            for name, value in (
                ("WECOM_MSGAUDIT_CORPID", self.corpid),
                ("WECOM_MSGAUDIT_SECRET", self.secret),
                ("WECOM_MSGAUDIT_PRIVATE_KEY", self.key_path),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("缺少存档配置（写在 .env）: " + ", ".join(missing))
        if not os.path.exists(self.key_path):
            raise FileNotFoundError(f"私钥文件不存在: {self.key_path}")

        self._privkey = _load_private_key(self.key_path)
        self._sdk = _WeComFinanceSdk(_find_dll())
        self._sdk.init(self.corpid, self.secret)
        self._seq = self._load_seq()
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        print(f"存档长轮询已启动，起始 seq={self._seq}")

    def _load_seq(self) -> int:
        if os.path.exists(SEQ_FILE):
            try:
                with open(SEQ_FILE, "r", encoding="utf-8") as f:
                    return int(json.load(f).get("seq", 0))
            except Exception:
                return 0
        return 0

    def _save_seq(self, seq: int) -> None:
        with open(SEQ_FILE, "w", encoding="utf-8") as f:
            json.dump({"seq": seq, "time": int(time.time())}, f)

    def fetch_messages(self) -> List[IncomingMessage]:
        out = []
        while True:
            try:
                out.append(self.incoming.get_nowait())
            except queue.Empty:
                break
        return out

    def _poll_loop(self) -> None:
        while self._running:
            try:
                self._pull_once()
            except Exception as e:
                print("存档拉取异常:", e)
            time.sleep(POLL_INTERVAL)

    def _pull_once(self) -> None:
        payload = self._sdk.get_chat_data(self._seq, PULL_LIMIT)
        items = payload.get("chatdata") or []
        max_seq = self._seq
        for item in items:
            try:
                msg = self._to_message(item)
            except Exception as e:
                print("存档解密/解析失败:", e)
                continue
            max_seq = max(max_seq, int(item.get("seq", max_seq)))
            if msg is not None:
                try:
                    self.incoming.put_nowait(msg)
                except queue.Full:
                    print("存档队列已满，丢弃旧消息")
                    try:
                        self.incoming.get_nowait()
                    except queue.Empty:
                        pass
                    self.incoming.put_nowait(msg)
        if max_seq != self._seq:
            self._seq = max_seq
            self._save_seq(max_seq)

    def _random_key(self, encrypt_random_key: str) -> List[bytes]:
        from cryptography.hazmat.primitives.asymmetric import padding

        raw = self._privkey.decrypt(
            base64.b64decode(encrypt_random_key), padding.PKCS1v15()
        )
        variants = [raw]
        try:
            variants.append(base64.b64decode(raw))
        except Exception:
            pass
        return variants

    def _decrypt_item(self, item: dict) -> dict:
        erk = item.get("encrypt_random_key") or ""
        ecmsg = item.get("encrypt_chat_msg") or ""
        last_error = None
        for candidate in self._random_key(erk):
            try:
                return self._sdk.decrypt(candidate, ecmsg)
            except Exception as e:  # 私钥解密结果形态不同，试两种
                last_error = e
        raise last_error or RuntimeError("DecryptData 失败")

    def _to_message(self, item: dict) -> Optional[IncomingMessage]:
        body = self._decrypt_item(item)
        if body.get("action") not in (None, "send"):
            return None
        roomid = body.get("roomid") or ""
        if not roomid:
            return None  # 只处理群聊
        chain = self._to_chain(body)
        if not chain:
            return None
        msgtime = int(body.get("msgtime") or time.time() * 1000)
        sender = body.get("from") or ""
        self.seen_rooms[roomid] = {
            "roomid": roomid,
            "msgtype": body.get("msgtype"),
            "sender": sender,
            "members": body.get("tolist") or [],
        }
        return IncomingMessage(
            group_id=roomid,
            sender_id=sender,
            message_id=body.get("msgid") or str(item.get("seq")),
            timestamp=msgtime // 1000,
            chain=chain,
            raw=body,
            is_group=True,
        )

    def _to_chain(self, body: dict) -> List[dict]:
        msgtype = body.get("msgtype")
        chain: List[dict] = []
        if msgtype == "text":
            text = ((body.get("text") or {}).get("content") or "").strip()
            if text:
                chain.append({"type": "Plain", "text": text})
        elif msgtype == "image":
            # 媒体下载需要 SDK 返回数据长度，官方 API 没给，这里只占位不入库。
            sdkfileid = (body.get("image") or {}).get("sdkfileid")
            if sdkfileid:
                chain.append({"type": "Image", "sdkfileid": sdkfileid})
        elif msgtype == "mixed":
            for entry in (body.get("mixed") or {}).get("item") or []:
                kind = entry.get("type")
                content = entry.get("content")
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except json.JSONDecodeError:
                        content = {}
                content = content or {}
                if kind == "text":
                    text = (content.get("content") or "").strip()
                    if text:
                        chain.append({"type": "Plain", "text": text})
                elif kind == "image" and content.get("sdkfileid"):
                    chain.append({"type": "Image", "sdkfileid": content["sdkfileid"]})
        return chain

    def dump_rooms(self) -> Dict[str, dict]:
        """存档不返回群名，所以这里列出见到的 roomid，靠内容/成员认。"""
        return dict(self.seen_rooms)

    def send_chain(
        self,
        target: Any,
        chain: List[dict],
        *,
        is_group: bool = True,
    ) -> Optional[Any]:
        from webhook import resolve_key, send_chain as webhook_send

        key = resolve_key(target)
        if not key:
            print(
                f"没配 {target} 的 webhook key：在 config.json 的 webhook_keys 里填上，"
                "否则存档只能收不能发。"
            )
            return None
        return webhook_send(key, chain)

    def close(self) -> None:
        self._running = False
        if self._sdk:
            self._sdk.close()
