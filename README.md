# BG 积分 bot

一个只会算分的企业微信群机器人：维护 **IEG / CDG / TEG / CSIG / WXG / PCG** 六个 BG 的积分榜。

传输方式是企微**智能机器人长连接**（`wss://openws.work.weixin.qq.com`，BotID + Secret 认证），
收发走 `aibot_subscribe` / `aibot_msg_callback` / `aibot_respond_msg` / `aibot_send_msg`，
对齐 [stock-assistant](https://github.com/dufolk/stock-assistant) 的 aibot 路径。

## 跑起来

1. 复制 `.env.example` 为 `.env`，填入 `WECOM_BOT_ID`、`WECOM_SECRET`（`.env` 不入库）。
2. `pip install -r requirements.txt`
3. `python main.py`

群里 **@机器人** 发一句话就能触发（群聊只有 @ 的消息会推回调，这是企微限制）。

## 支持的指令

| 说什么 | 效果 |
|---|---|
| `IEG加1分`、`给TEG加3分`、`WXG +5` | 加分；不写分数按 1 分算 |
| `CSIG扣2分`、`PCG减一分` | 扣分；支持数字和中文数字 |
| `WXG加2分 PCG扣1分` | 一次多个 BG，分别结算后回一张榜 |
| `积分榜` / `排行榜` / `分数` / `排名` | 只看榜 |
| `IEG清零` / `清零` | 单个或全部归零 |
| `help` | 用法说明 |

单次最多 ±999，数字必须是紧跟操作符的那种（`2024给IEG加分` 里的 2024 不会被算进去）。

## 榜单长啥样

回复是 **markdown**：标题加粗 + 逐行引用 + `<font color>` 上色。

```text
**BG 积分榜**
> **IEG** +3 → +15 分 · 第 1 名
> 🥇 **IEG** +15 ▉▉▉▉▉▉▉▉▉▉
> 🥈 **WXG** +8 ▉▉▉▉▉
> 3. **CDG** +2 ▉
> 4. **TEG** 0
> 5. **PCG** 0
> 6. **CSIG** -3 ▉▉
<font color="comment">会话 …abc123 · 更新 09-17 15:04</font>
```

颜色：`info` 绿（正分）、`warning` 橙红（负分）、`comment` 灰（0 分）。

**关于 HTML**：企微不是真的支持 HTML，只认 `<font color="info|warning|comment">` 这一个标签配合 markdown
（V1 子集：加粗、引用、链接、font color；标题/代码块/表格/列表在 V1 里不保证渲染）。
想要表格可以试 `markdown_v2`（客户端 4.1.38+），但不确定所有机器人的 API 版本都认——
`.env` 里设 `WECOM_MD_MSGTYPE=markdown_v2` 就能切，不好用删掉即可。

## 数据

分数存在 `scores.json`（按会话 chatid 分开，已 gitignore），含最近 200 条变更日志。
控制台里敲 `board` 可以打印最近会话的榜单，`peek` 开一个只收不发的探测窗口。

## 结构

| 文件 | 作用 |
|---|---|
| `main.py` | 入口：长连接收发循环 + 控制台指令 |
| `scorebot.py` | 指令解析、积分存储、榜单渲染 |
| `wecom_adapter.py` | 企微长连接适配器 |
| `adapter.py` | 平台接口（`IncomingMessage`） |
| `configutil.py` | `.env` 加载 + JSON 读写 |

## 许可证

AGPL-3.0（见 `LICENSE`）。
