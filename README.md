# Virtual Friends

一个基于 NoneBot2 的 AI 好友插件，让机器人拥有可编辑的人设、持续积累的记忆，以及文字、图片和语音表达能力。

支持 QQ 群聊、C2C 私聊、频道与频道私信；群聊和私聊支持 Qwen 语音合成。

## 功能

- **自定义人设**：创建、查看和切换提示词，为不同会话设置不同角色。
- **长期记忆**：对话按细节、叙事与整体印象分层整理，并提取重要事实。
- **图片理解**：结合图片进行聊天，通过 DeepSeek Files API 保存图片引用。
- **知识库**：检索本地 Markdown 资料，可选重排序，并可发送资料中的图片。
- **语音回复**：AI 根据语境选择发送文字或语音，也支持指定文字的语音测试。
- **主动发言**：在配置的时段内发起话题，支持闲聊与长时间沉默后的唤醒。
- **每日总结**：生成图文日报，支持手动生成和定时发送。
- **会话隔离**：不同应用、群聊、私聊及频道分别管理配置和记忆。

## 安装

需要 Python 3.10 或更高版本，以及已配置 QQ 官方适配器的 NoneBot2 项目。

在机器人项目根目录执行：

```bash
git clone https://github.com/su-liu-guang/Virtual_friends_qq.git src/plugins/Virtual_friends_qq
python -m pip install -r src/plugins/Virtual_friends_qq/requirements.txt
python -m pip install 'nonebot2[fastapi,httpx,websockets]>=2.5.0'
```

在项目 `pyproject.toml` 中启用插件目录，已有配置时合并而不是覆盖：

```toml
[tool.nonebot]
plugin_dirs = ["src/plugins"]
```

使用日报功能前安装浏览器及其系统依赖：

```bash
python -m playwright install --with-deps chromium
```

## 连接与模型配置

将 [.env.qq.example](.env.qq.example) 中需要的配置合并到机器人实际加载的环境文件，例如 `.env.prod`。保留现有 QQ 应用凭据，不要直接覆盖整个文件。

```dotenv
DRIVER=~fastapi+~httpx+~websockets
COMMAND_START=["/"]
SUPERUSERS=["qq:你的用户OpenID"]
QQ_BOTS=[{"id":"应用ID","secret":"应用Secret","intent":{"c2c_group_at_messages":true,"at_messages":true,"direct_message":true}}]

CHAT_API_KEY=你的模型密钥
CHAT_API_URL=https://api.deepseek.com
CHAT_MODEL_NAME=deepseek-v4-flash-vision-exp
# 思考开关和等级独立配置，修改后重启生效
CHAT_THINKING_ENABLED=false
# low / high / max，仅开启思考时生效
CHAT_REASONING_EFFORT=high

EMBEDDING_API_KEY=你的向量模型密钥
EMBEDDING_API_URL=https://api.siliconflow.cn/v1
EMBEDDING_MODEL_NAME=BAAI/bge-m3

# Qwen TTS：独立配置百炼密钥与已创建的复刻音色
TTS_API_KEY=你的百炼密钥
TTS_VOICE_ID=你的复刻音色ID
TTS_WS_URL=wss://dashscope.aliyuncs.com/api-ws/v1/inference
```

文本聊天使用 OpenAI 兼容接口。图片理解和图片持久化需要相应的视觉模型与 DeepSeek Files API 支持；语音合成仅使用 Qwen WebSocket，模型固定为 `qwen-audio-3.0-tts-plus`。须单独填写百炼 `TTS_API_KEY`，不回退使用聊天密钥。旧 `TTS_API_URL` 不再使用。

重排序为可选功能，可通过 `RERANKER_API_KEY`、`RERANKER_API_URL`、`RERANKER_MODEL` 配置。QQ 事件订阅与媒体权限需在应用侧开通。

启动机器人：

```bash
nb run
```

### 聊天思考设置

`CHAT_THINKING_ENABLED` 默认 `false`，控制聊天回复是否启用思考；`CHAT_REASONING_EFFORT` 默认 `high`，可选 `low`、`high`、`max`，仅在思考开启时发送给模型。修改这两项环境变量后需要重启，`/重载配置` 用于重新加载人设与会话配置。

## 开始聊天

1. 发送 `/vf身份` 获取用户 OpenID 和当前会话标识；群聊中按平台要求 @ 机器人。
2. 如需超级用户权限，将对应 OpenID 写入 `SUPERUSERS` 并重启。
3. 群主、管理员或超级用户发送 `/人生启动` 开启群聊。频道由超级用户开启，私聊默认可聊天。
4. 发送 `/vf帮助` 查看指令，使用 `/切换人设 名称` 选择角色。

机器人只会处理 QQ 平台实际推送的消息。如果应用仅能收到 @ 消息，记忆与日报也只包含这些消息。

## 指令

| 权限 | 指令 | 用途 |
| --- | --- | --- |
| 所有用户 | `/vf帮助` | 查看指令菜单 |
| 所有用户 | `/vf身份` | 查看 OpenID 和会话标识 |
| 所有用户 | `/记忆状态` | 查看记忆统计 |
| 所有用户 | `/查看提示词 [名称]` | 查看人设提示词 |
| 管理员、群主、超管 | `/人生启动`、`/世界终结` | 开启或关闭群聊 |
| 管理员、群主、超管 | `/提示词列表`、`/切换人设 名称` | 查看和选择人设 |
| 管理员、群主、超管 | `/添加提示词 名称 内容` | 添加或更新人设 |
| 管理员、群主、超管 | `/清空记忆` | 清空当前会话记忆 |
| 管理员、群主、超管 | `/查看配置`、`/修改配置 JSON` | 管理当前会话配置 |
| 管理员、群主、超管 | `/重载配置` | 重新读取配置和提示词 |
| 管理员、群主、超管 | `/测试语音 文字` | 合成指定文字并发送语音 |
| 仅超管 | `/删除提示词 名称`、`/查看白名单` | 删除人设、查看启用列表 |
| 仅超管 | `/今日总结`、`/昨日总结` | 生成日报 |
| 仅超管 | `/测试日报 日期 [会话标识]` | 生成指定日期的日报 |

群主和管理员权限仅在群聊内有效；私聊和频道中的管理指令需要超级用户权限。会话配置指令要求该会话已启用。

## 人设与语音

配置位于机器人工作目录下的 `data/Virtual_friends/personas.json`：

```json
{
  "_voice_prompt": "只输出 JSON：{\"text\":\"回复正文\",\"delivery\":\"text\"}。delivery 只能是 text 或 voice。根据语境选择消息类型；用户要求语音时选择 voice，要求文字时选择 text。",
  "default": {
    "prompt": "你是一个友好、乐于助人的 AI 好友。"
  }
}
```

`_voice_prompt` 是所有人设共用的语音判断提示词，始终放在第一项，不显示在人设列表中。修改后发送 `/重载配置` 即可生效。每个人设只需要 `prompt` 字段。

AI 输出的 `delivery` 为 `text` 时发送文字，为 `voice` 时合成语音；程序不额外判断情绪分数或语音冷却。语音开关优先于消息类型：未设置或关闭时，普通回复与主动发言均只发送文字，语音测试会提示先开启。新会话默认关闭语音，已有会话明确设置为 true 的保持启用。语音支持群聊和 C2C 私聊，频道使用文字。

```text
/测试语音 你好呀，今天见到你真的很开心！
/修改配置 {"语音开关":true,"语音音色":"你的复刻音色ID"}
/修改配置 {"语音开关":false}
```

开关按会话保存在 `data/Virtual_friends/groups.json` 中，每个群可独立设置：

```json
{
  "qq:应用ID:group:群A的OpenID": {"voice_enabled": true},
  "qq:应用ID:group:群B的OpenID": {"voice_enabled": false}
}
```

请在已有群配置中修改该字段，保留其他字段。手动编辑后执行 `/重载配置`；群内 `/修改配置 {"语音开关":true}` 只影响当前群。旧配置缺少此字段时自动补为 `false`。

复刻音色创建时 `target_model` 必须为 `qwen-audio-3.0-tts-plus`，查询状态为 `OK` 后使用返回的 `voice_id`。群内音色留空时使用 `TTS_VOICE_ID`；旧配置里的 `stella/tide` 也会使用该全局音色。缺少音色时合成失败并回退文字。`/语音测试` 是测试指令的别名，直接朗读指定文字，不经过 AI 判断，但仍须启用语音开关。

单次合成支持 1—500 字，超时 45 秒，音频上限 120 秒。合成失败或 QQ 明确拒绝时，普通回复回退为文字；发送结果不明时不自动补发。音频在内存中生成，不保留临时文件。聊天记忆保存回复正文。

## 主动发言与日报

两项功能默认关闭，可分别启用：

```text
/修改配置 {"主动发言开关":true}
/修改配置 {"每日总结开关":true,"每日总结时间":"23:00"}
```

主动发送受 QQ 应用权限和平台额度限制。定时任务使用进程本地时区，国内部署建议设置 `TZ=Asia/Shanghai`。日报依赖浏览器，实际媒体发送效果需在自己的应用下验证。

## 数据与知识库

以下路径均相对于机器人工作目录：

| 路径 | 内容 |
| --- | --- |
| `data/Virtual_friends/personas.json` | 人设与语音判断提示词 |
| `data/Virtual_friends/groups.json` | 会话配置、白名单 |
| `data/Virtual_friends/memory.db` | 聊天记录、摘要、重要事实及图片索引 |
| `data/Virtual_friends/knowledge/` | Markdown 知识库及相关图片 |
| `data/Virtual_friends/pending_images/` | 等待上传的图片 |
| `data/Virtual_friends/logs/cache_metrics.jsonl` | 模型缓存用量日志 |

知识库中以 `_` 开头的文件或目录不会加载；移除前缀后会重新加入索引。部署迁移时应备份整个数据目录。

### 记忆整理

| 层级 | 整理时机 | 内容 |
| --- | --- | --- |
| L1 | 每 50 条消息 | 近期详细摘要 |
| L2 | 每 20 条 L1 | 叙事概括 |
| L3 | 每 10 条 L2 | 长期整体印象 |

聊天上下文包含全部未归档 L1、L2，以及最新 5 条 L3；当前摘要周期内尚未处理的消息按时间追加。摘要最多尝试 6 次，连续失败时保留原消息和摘要，不提前归档。

图片首次通常内联发送，同时在后台上传并缓存文件引用；后台任务持久化保存，重启后可继续。内联原图总量超过 30 MiB 时，超出预算的图片需要先上传再引用。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `__init__.py`、`qq_support.py` | 指令、消息处理、QQ 会话标识与发送 |
| `clients.py`、`tts.py` | 聊天、向量接口与 Qwen 语音合成 |
| `config.py`、`logic.py` | 配置、人设上下文和回复处理 |
| `database.py`、`scheduler.py` | 数据模型与分层记忆整理 |
| `image_upload.py`、`knowledge.py` | 图片持久化上传与知识库检索 |
| `active_behavior.py`、`summary.py` | 主动发言与日报 |
| `cache_metrics.py` | 模型缓存用量记录 |

## 调试与测试

设置 `LOG_LEVEL=DEBUG` 可查看 AI 返回的原始正文，包括语音消息类型。日志可能含聊天内容，分享前请自行检查。

在已安装插件的机器人项目根目录运行：

```bash
python src/plugins/Virtual_friends_qq/tests/test_qq_adapter.py
python src/plugins/Virtual_friends_qq/tests/test_cache_summary.py
python src/plugins/Virtual_friends_qq/tests/test_tts.py
```

测试使用模拟 QQ 请求和本地合成服务，不消耗真实模型额度。真实图片理解、语音播放和日报发送需要另外联调。

### Qwen 接入说明

使用现有 aiohttp 异步 WebSocket 接收 PCM，完整结束后封装为 24 kHz、16 位单声道 WAV 发送。每次合成独立连接；保留 500 字、45 秒请求超时、120 秒音频和最多 3 个并发限制。

默认地址为北京地域。建议将 `TTS_WS_URL` 配置为 `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference`；新加坡使用 `wss://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/inference`。密钥、音色和接入地域/业务空间应匹配。更改环境变量后重启机器人，再用 `/测试语音 你好` 验证。

协议参考：[WebSocket 接入](https://docs.bailian.console.aliyun.com/zh/model-studio/qwen-audio-tts-websocket-api)、[音色复刻](https://docs.bailian.console.aliyun.com/zh/model-studio/voice-clone-design-http-api)。复刻只需预先创建，日常合成重复使用音色 ID。
