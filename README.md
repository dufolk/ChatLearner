# ChatLearner

基于会话词库的学习型聊天机器人精简版：**只保留文本与图片的学习 / 回复**。

核心与平台解耦。默认入口使用占位适配器，接入 Discord / Telegram / OneBot 等时实现 `BotAdapter` 即可。

## 结构

| 文件 | 作用 |
|------|------|
| `main.py` | 控制台入口 |
| `engine.py` | 学习链 + 精确匹配回复 |
| `wordstock.py` | 词库读写（`WordStock/<会话ID>.cl`） |
| `message.py` | 只保留 Plain / Image |
| `adapter.py` | 平台适配器接口 |
| `example_adapter.py` | 占位适配器 / 接入模板 |
| `config.json` | 运行配置 |

## 快速开始

```bash
python main.py
```

在 `main.py` 里把 `ExampleAdapter()` 换成你的平台适配器。控制台：

```text
add both <会话ID>
learning
reply
```

## 接到其他平台

实现 `adapter.BotAdapter` 三个方法：

- `connect()`
- `fetch_messages() -> list[IncomingMessage]`
- `send_chain(target, chain, is_group=True)`

消息链统一用：

```python
[{"type": "Plain", "text": "你好"}]
[{"type": "Image", "url": "https://..."}]
# 或 {"type": "Image", "path": "/abs/path.png"}
```

参考 `example_adapter.py`。

## 行为说明

- **学习**：同一会话内，间隔超过 `interval` 秒后的第一条算新问题；之后每条既是上一条的答案，也是新问题。只记文本和图片。
- **回复**：对当前消息做**精确匹配**（去掉图片 url 后的链），按答案权重 `same` 随机抽取一条发送。
- 词库格式兼容旧版 `.cl` pickle；新写入的答案用 JSON 字符串存储。

## 许可证

AGPL-3.0（见 `LICENSE`）。基于原 ChatLearning 项目精简而来，请保留原作者版权声明。
