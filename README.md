# ChatLearner

基于会话词库的学习型聊天机器人精简版：**文本 + 图片**，接入企业微信智能机器人长连接。

## 两种接入模式

| | 模式一：智能机器人长连接 | 模式二：会话内容存档 |
|---|---|---|
| 群里要不要 @ | **要 @** 才收得到 | **不用 @**，全群消息都收 |
| 凭据 | BotID + Secret | corpid + 存档 Secret + RSA 私钥 |
| 额外依赖 | 无 | 官方原生 SDK（dll/so） |
| 保留时长 | 实时 | 只回溯 5 天 |
| 推荐场景 | 问答型机器人 | 学习型（要整群语料） |

`.env` 里填了 `WECOM_MSGAUDIT_CORPID` 就自动走模式二，否则走模式一。

## 模式一：智能机器人（默认）

1. 管理后台 → 智能机器人 → 创建机器人 → 开启 **API 模式 → 长连接**，拿到 BotID 和 Secret，填进 `.env`。
2. `pip install -r requirements.txt`
3. `python main.py`

回调字段只有 `chatid` / `chattype` / `from.userid`，**没有群名**。把机器人拉进「6组！」，群里 @它 一句，控制台会打印：

```text
企微收消息 chattype=group chatid=wrxxxx from=zhangsan msgtype=text quote=False
```

然后 `add both wrxxxx`，或敲 `bind 6组！ wrxxxx` 用群名代替。

## 模式二：会话内容存档（不用 @）

### 怎么领这些东西

1. **开通**：管理后台 → **安全与管理 → 管理工具 → 会话内容存档**，开通并把目标群的内部成员纳入授权范围（发送方或接收方只要在范围内，该消息就会存档；群外/外部成员不在范围内的话，这条消息拿不到）。
2. **corpid**：我的企业 → 企业信息 → 最下面的「企业ID」，形如 `wwd08c8exxxx5ab44d`。
3. **存档 Secret**：会话内容存档页面里的 **Secret**（跟应用 Secret 不是一个）。
4. **RSA 密钥对**：自己生成，**私钥留在本地**，**公钥**粘贴到存档页面并记下版本号：
   ```bash
   openssl genrsa -out keys/msgaudit_private.pem 2048
   openssl rsa -in keys/msgaudit_private.pem -pubout -out keys/msgaudit_public.pem
   ```
5. **原生 SDK**：官方文档中心 → 会话内容存档 → 下载 SDK，把 `WeWorkFinanceSdk.dll`（Windows）或 `libWeWorkFinanceSdk.so`（Linux）放到项目根或 `sdk/`，也可用 `WECOM_SDK_DLL` 指绝对路径。
6. 把 2、3、4、5 填进 `.env`（见 `.env.example`），`python main.py`。

### 找不到群名的问题

存档 API 只给 `roomid`（`wr` 开头），**不返回「6组！」这个显示名**。启动后让群里随便说几句，然后：

```text
rooms                      # 列出见到的 roomid + 最近发言者/成员
bind 6组！ wrXXXXXXXXXX    # 绑定，之后 add both 6组！ 即可
add both 6组！
learning
```

### 发消息（重要）

存档**只能收不能发**。回复必须另挂一个发送通道，推荐**群机器人 Webhook**：

1. 「6组！」群设置 → **群机器人** → 添加 → 复制它的 Webhook 地址里 `key=` 后面那串。
2. `hook 6组！ <那串key>`（或写进 `config.json` 的 `webhook_keys`）。

之后所有回复都走这个 key：**不需要 @、不需要事先交互**，限 20 条/分钟。没配 key 时，模式一还会退回 `aibot_send_msg`（但那要求群里先 @ 过一次）。

## 结构

| 文件 | 作用 |
|------|------|
| `main.py` | 控制台入口（按 .env 自动选模式） |
| `wecom_adapter.py` | 智能机器人长连接适配器（要 @） |
| `msgaudit_adapter.py` | 会话存档适配器（不用 @，只收） |
| `webhook.py` | 群机器人 Webhook 发送 |
| `engine.py` | 学习链 + 精确匹配回复 |
| `wordstock.py` | 词库 |
| `message.py` | Plain / Image |
| `adapter.py` | 平台接口 |
| `config.json` | 运行配置 |

## 群消息覆盖范围（企微硬限制）

- 模式一：群聊里**只有 @机器人** 的消息会推回调。变通玩法是 **引用某条消息 + @机器人**，回调里的 `quote` 带被引用原文，程序已把它并入学习链。
- 模式二：全群消息都会收，但只能回溯 **5 天**，且需要企业先开通存档、配置可调用 IP。
- 存档路径目前只做文本：图片消息会保留 `sdkfileid` 占位（官方 SDK 的媒体接口不返回长度，解码不可靠），不入库。
- 想要「不用 @ 又能发言」以外的东西，比如全量历史，只能按期把存档数据落库，别指望实时 API。

## 行为说明

- **学习**：同一会话内，间隔超过 `interval` 秒后的第一条算新问题；之后每条既是上一条的答案，也是新问题。
- **回复**：精确匹配后按权重抽取一条发送，发送顺序是 webhook → `aibot_respond_msg`（带回调 `req_id`）→ `aibot_send_msg`。
- 频率：webhook 20 条/分钟；智能机器人 30 条/分钟、1000 条/小时。

## 许可证

AGPL-3.0（见 `LICENSE`）。基于原 ChatLearning 项目精简而来，请保留原作者版权声明。
