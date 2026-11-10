# 更新日志 (Changelog)

所有关于 `astrbot_plugin_qq_poke_react`（QQ 原生戳一戳与表情表态工具箱）插件的变更记录都将记录在此文件中。

本项目遵循 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/) 规范。

---

## [1.0.0] - 2026-11-10

### 🚀 初始版本正式发布 (Initial Release)

`astrbot_plugin_qq_poke_react` 是一款专为 AstrBot 与 NapCat (NTQQ) 深度适配的原生动作引擎与拟人化互动插件。通过打通 OneBot 11 底层扩展协议，摆脱传统纯文本聊天机器人的枯燥单一性，赋予机器人原生的“戳一戳”、表情点赞表态、被动反戳与大模型主动情绪表达能力。

---

### ✨ 核心特性与功能亮点

#### 1. NTQQ 原生动作引擎 (Native Action Engine)
- **群聊与私聊全场景支持**：无缝调用 NapCat 扩展 API `group_poke` 与 `friend_poke`，实现毫秒级响应的原生“窗口抖动/拍了拍”物理戳一戳交互。
- **消息表情点赞表态 (Emoji Like)**：直通 NTQQ `set_msg_emoji_like` 协议，可在群聊或私聊中对任意目标消息贴上表情表态（如点赞 👍、大笑 😂、抱拳 👊、爱心 ❤️、打脸 🤡、666 等）。
- **智能协议回退机制**：自动识别 `aiocqhttp` OneBot 11 适配器连接，优雅兼容不同版本 NapCat 与 Lagrange 动作派发。

#### 2. 智能表情别名解析词典 (Smart Emoji Resolver)
- **多维度映射**：内置覆盖 100+ 常见 NTQQ 原生表情的别名对照词典，支持多种输入形式：
  - 中文别名：如 `赞`、`点赞`、`强`、`大笑`、`哭笑`、`笑哭`、`玫瑰`、`爱心`、`心碎`、`抱拳`、`打脸`、`滑稽`、`牛逼`、`666` 等；
  - Unicode Emoji：直接传入 `👍`、`❤️`、`😂`、`🎉`、`🔥`、`💩`、`👏` 等符号自动精准转换为 QQ face ID；
  - 原生数值 ID：直接传入 QQ 表情数字代码（如 `76`、`124`、`212` 等）；
- **容错与友好提示**：未收录的单个 Unicode 表情自动转换为十进制码点（emoji_type=2）；完全无法识别的输入返回带候选建议的错误提示，绝不静默替换成其他表情。

#### 3. 内存级防风控滑动窗口限频器 (Anti-Risk Rate Limiter)
- **细粒度按目标隔离**：以 `(session_id, target_user_id)` 与 `(session_id, message_id)` 为键值维护独立滑动冷却时间窗口。
- **可定制冷却周期**：
  - 戳一戳目标冷却（默认 3 秒，范围 1~30 秒）；
  - 表情表态冷却（默认 1 秒，范围 1~10 秒）；
- **管理员特权沙箱放行**：内置 `admins_id` 白名单机制，管理员执行操作享受零延迟与限频特权豁免。

#### 4. 双 LLM 函数调用工具 (Native LLM Tools)
- **`poke_user` 工具**：
  - 允许大模型在理解上下文情境后自主触发物理戳一戳；
  - 参数包含目标用户 QQ 号 `target_id` 及可选群号 `group_id`；
  - 适用于提醒群友、打招呼、调皮互动、撒娇唤醒等情景。
- **`react_to_message` 工具**：
  - 赋予大模型根据上下文对特定消息贴表情进行情绪表态的能力；
  - 参数包含目标消息 ID `message_id` 与表情别名/ID `emoji_id`；
  - 适用于对群友的精彩发言点赞 👍、对幽默笑话贴 😂、对赞叹贴 🎉 等。

#### 5. 双前缀快捷聊天指令 (User Chat Commands)
- **`/poke [@用户 / QQ号]`**：
  - 快捷戳人指令，支持艾特群友、直接输入数字 QQ 号，若不填目标则默认戳发送者自身。
- **`/react <表情名/Emoji/ID> [消息ID]`**：
  - 快捷表情表态指令；在回复某条群消息时直接输入 `/react 赞` 或 `/react 😂`，即可自动提取回复链中的被引消息 ID 并完成贴表情。

#### 6. 被动被戳自动拟人化反戳 (Auto Counter-Poke)
- **被动通知事件监听器**：实时感知 OneBot 11 `notify.poke` 事件；
- **智能目标鉴别**：精确判定被戳目标是否为机器人自身账号（`target_id == bot_self_id`）；
- **可配置拟人延迟与台词**：
  - 支持后台可配反戳延迟时间 `counter_poke_delay`（默认 0.8s，模拟真实人类交互手速）；
  - 支持个性化反戳台词 `counter_poke_text`（默认 `"哼，反戳你一下！"`，支持留空仅纯反戳）；
  - 支持一键开关 `auto_counter_poke`。

---

### 📦 资产规范与合规性
- 完整包含官方 256x256 图标 `logo.png`；
- 遵循 AstrBot 核心插件规范，完整适配 `_PluginUpdater.inspect_plugin_directory` 与 `AstrBotConfig._config_schema_to_default_config`；
- 附带标准化开源许可证 `LICENSE` (MIT) 与全面的中英双语 `README.md` 文档。
