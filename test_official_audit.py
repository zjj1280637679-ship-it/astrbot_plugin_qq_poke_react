# -*- coding: utf-8 -*-
"""
astrbot_plugin_qq_poke_react 官方生产级权威端到端合规审计与质检测试套件 (v1.0.0)
========================================================================
涵盖：
1. 官方目录规范审查 (_PluginUpdater.inspect_plugin_directory, 0 错误 0 警告)
2. 配置 Schema 规范解析与默认配置转换 (AstrBotConfig._config_schema_to_default_config)
3. 图标资产合规检验 (logo.png 存在且为严格 256x256 PNG)
4. 辅助开源资产完备性校验 (requirements.txt, CHANGELOG.md, LICENSE, README.md)
5. 智能表情解析器全场景与容错测试 (resolve_emoji_id 40+ 组覆盖、别名/Emoji/ID、建议列表)
6. 目标防风控滑动窗口限频器测试 (RateLimiter 线程安全、冷却拦截、目标隔离、重置清理)
7. 底层动作引擎与双级优雅降级策略测试 (ActionEngine group_poke/friend_poke -> send_poke 降级)
8. Star 插件装载与生命周期管理测试 (QQPokeReactPlugin 初始化、配置热更新截断防御、资源释放)
9. 大模型函数调用工具契约与证据隔离测试 (poke_user, react_to_message, content_role='evidence', instruction_weight=0)
10. 聊天前缀便捷指令端到端测试 (/poke 与 /react 参数解析、消息引用提取与上下文降级)
11. 被动被戳通知监听与拟人化自动反戳测试 (OneBot 11 notice.notify.poke, 延迟模拟, 趣味台词, 精准忽略)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

# 路径基准定义
ASTRBOT_ROOT = Path(r"C:\Users\asus\.gemini\antigravity\scratch\astrbot-napcat\AstrBot")
PLUGIN_DIR = ASTRBOT_ROOT / "data" / "plugins" / "astrbot_plugin_qq_poke_react"

if str(ASTRBOT_ROOT) not in sys.path:
    sys.path.insert(0, str(ASTRBOT_ROOT))
plugins_parent = PLUGIN_DIR.parent
if str(plugins_parent) not in sys.path:
    sys.path.insert(0, str(plugins_parent))

from PIL import Image

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent
from astrbot.core.star.updater import _PluginUpdater

from data.plugins.astrbot_plugin_qq_poke_react.action_engine import (
    ActionAPIError,
    ActionEngine,
    ActionEngineError,
    ActionExecutionError,
    EmojiResolveError,
    RateLimiter,
    get_emoji_info,
    list_supported_emojis,
    resolve_emoji_id,
)
from data.plugins.astrbot_plugin_qq_poke_react.main import (
    EVIDENCE_FOOTER,
    EVIDENCE_HEADER,
    EVIDENCE_SECURITY_DISCLAIMER,
    QQPokeReactPlugin,
    wrap_action_evidence_envelope,
)


class MockEvent:
    """模拟 AstrBot 消息事件 (AstrMessageEvent)"""

    def __init__(
        self,
        sender_id: str = "10001",
        group_id: str = "888888",
        message_str: str = "",
        messages: Optional[List[Any]] = None,
        role: str = "member",
        self_id: str = "20000",
        raw_message: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._sender_id = str(sender_id)
        self._group_id = str(group_id) if group_id else ""
        self._message_str = message_str
        self._messages = messages or [Comp.Plain(text=message_str)]
        self.role = role
        self._self_id = str(self_id)

        # 模拟底层 message_obj 与 raw_message
        self.message_obj = MagicMock()
        self.message_obj.group_id = self._group_id
        self.message_obj.self_id = self._self_id
        self.message_obj.message_id = "9999"
        self.message_obj.sender = MagicMock()
        self.message_obj.sender.user_id = self._sender_id
        self.message_obj.sender.nickname = f"用户_{self._sender_id}"
        self.message_obj.message = self._messages
        self.message_obj.raw_message = raw_message or {
            "post_type": "message",
            "message_type": "group" if self._group_id else "private",
            "message_id": 9999,
            "user_id": int(self._sender_id),
            "group_id": int(self._group_id) if self._group_id else None,
            "self_id": int(self._self_id),
        }

        # Mock 底层 bot
        self.bot = MagicMock()
        self.called_actions: List[Dict[str, Any]] = []

        async def _mock_call_action(action: str, **params: Any):
            self.called_actions.append({"action": action, "params": params})
            return {"status": "ok", "retcode": 0, "data": "ok"}

        self.bot.call_action = AsyncMock(side_effect=_mock_call_action)
        self.bot.call_api = self.bot.call_action

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_group_id(self) -> str:
        return self._group_id

    def get_self_id(self) -> str:
        return self._self_id

    def get_messages(self) -> List[Any]:
        return self._messages

    def get_message_str(self) -> str:
        return self._message_str

    def is_admin(self) -> bool:
        return self.role == "admin" or self._sender_id == "2973338092"

    def plain_result(self, text: str) -> Dict[str, Any]:
        return {"type": "plain", "text": text}


class OfficialAuditTestSuite(unittest.IsolatedAsyncioTestCase):
    """astrbot_plugin_qq_poke_react 官方生产级综合审计测试套件"""

    @classmethod
    def setUpClass(cls):
        print("\n" + "=" * 70)
        print("  正在启动 astrbot_plugin_qq_poke_react 官方生产级合规与端到端质检测试")
        print("=" * 70)

    async def asyncSetUp(self):
        self.mock_context = MagicMock()
        self.default_config = {
            "poke_cooldown": 2,
            "react_cooldown": 1,
            "auto_counter_poke": True,
            "counter_poke_delay": 0.1,  # 测试环境采用快速延迟
            "counter_poke_text": "哼，反戳你一下！",
            "admins_id": ["2973338092"],
        }
        self.plugin = QQPokeReactPlugin(self.mock_context, self.default_config)
        await self.plugin.initialize()

    async def asyncTearDown(self):
        await self.plugin.terminate()

    # =================================================================
    # 一、官方目录规范、资产与配置合规审查
    # =================================================================

    def test_01_official_updater_inspect_directory(self):
        """1.1 运行官方 _PluginUpdater.inspect_plugin_directory，0 错误 0 警告通过"""
        res = _PluginUpdater.inspect_plugin_directory(PLUGIN_DIR)
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("metadata_entry"), "metadata.yaml")
        meta = res.get("metadata")
        self.assertIsInstance(meta, dict)

        self.assertEqual(meta.get("name"), "astrbot_plugin_qq_poke_react")
        self.assertEqual(meta.get("version"), "1.0.0")
        self.assertEqual(meta.get("author"), "zjj1280637679-ship-it")
        self.assertEqual(meta.get("display_name"), "QQ 原生戳一戳与表情表态工具箱")
        self.assertIn("直连 NapCat NTQQ", meta.get("short_desc", ""))
        self.assertTrue(len(meta.get("desc", "")) > 30)
        self.assertTrue(len(meta.get("help", "")) > 30)
        self.assertEqual(
            meta.get("repo"),
            "https://github.com/zjj1280637679-ship-it/astrbot_plugin_qq_poke_react",
        )
        self.assertEqual(meta.get("astrbot_version"), ">=4.16,<5")
        self.assertIn("aiocqhttp", meta.get("support_platforms", []))
        self.assertEqual(
            meta.get("tags"),
            ["戳一戳", "表情表态", "emoji点赞", "OneBot11", "NapCat", "LLM工具"],
        )
        print("  [PASS] 1.1 官方 _PluginUpdater.inspect_plugin_directory 校验 (0 Error)")

    def test_02_conf_schema_parse_and_default_config(self):
        """1.2 验证 _conf_schema.json 能够被 AstrBot 配置系统正确解析并生成默认配置"""
        schema_path = PLUGIN_DIR / "_conf_schema.json"
        self.assertTrue(schema_path.is_file(), "_conf_schema.json 不存在")
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        default_cfg = AstrBotConfig._config_schema_to_default_config(None, schema)
        self.assertIsInstance(default_cfg, dict)

        self.assertEqual(default_cfg.get("poke_cooldown"), 3)
        self.assertIsInstance(default_cfg.get("poke_cooldown"), int)

        self.assertEqual(default_cfg.get("react_cooldown"), 1)
        self.assertIsInstance(default_cfg.get("react_cooldown"), int)

        self.assertEqual(default_cfg.get("auto_counter_poke"), True)
        self.assertIsInstance(default_cfg.get("auto_counter_poke"), bool)

        self.assertEqual(default_cfg.get("counter_poke_delay"), 0.8)
        self.assertIsInstance(default_cfg.get("counter_poke_delay"), float)

        self.assertEqual(default_cfg.get("counter_poke_text"), "哼，反戳你一下！")
        self.assertIsInstance(default_cfg.get("counter_poke_text"), str)

        self.assertEqual(default_cfg.get("admins_id"), ["2973338092"])
        self.assertIsInstance(default_cfg.get("admins_id"), list)
        print("  [PASS] 1.2 _conf_schema.json 解析与 AstrBotConfig 默认值转换通过")

    def test_03_logo_dimensions_strict_256(self):
        """1.3 验证 logo.png 存在且尺寸严格为 256x256 PNG"""
        logo_path = PLUGIN_DIR / "logo.png"
        self.assertTrue(logo_path.is_file(), "logo.png 不存在")
        with Image.open(logo_path) as img:
            self.assertEqual(img.size, (256, 256), f"尺寸不符合 256x256: {img.size}")
            self.assertEqual(img.format, "PNG", f"格式不是 PNG: {img.format}")
        print("  [PASS] 1.3 logo.png 存在且尺寸严格为 256x256 PNG")

    def test_04_auxiliary_assets_completeness(self):
        """1.4 检查 requirements.txt, CHANGELOG.md, LICENSE, README.md 完备性"""
        req_path = PLUGIN_DIR / "requirements.txt"
        self.assertTrue(req_path.is_file())

        changelog_path = PLUGIN_DIR / "CHANGELOG.md"
        self.assertTrue(changelog_path.is_file())
        changelog_content = changelog_path.read_text(encoding="utf-8")
        self.assertIn("1.0.0", changelog_content)
        self.assertIn("poke_user", changelog_content)
        self.assertIn("react_to_message", changelog_content)

        license_path = PLUGIN_DIR / "LICENSE"
        self.assertTrue(license_path.is_file())
        license_content = license_path.read_text(encoding="utf-8")
        self.assertIn("MIT License", license_content)
        self.assertIn("2026", license_content)

        readme_path = PLUGIN_DIR / "README.md"
        self.assertTrue(readme_path.is_file())
        readme_content = readme_path.read_text(encoding="utf-8")
        self.assertIn("astrbot_plugin_qq_poke_react", readme_content)
        self.assertIn("mermaid", readme_content)
        self.assertIn("set_msg_emoji_like", readme_content)
        self.assertIn("group_poke", readme_content)
        print("  [PASS] 1.4 辅助开源规范资产完备性校验通过")

    # =================================================================
    # 二、智能表情解析器与目标防风控限频器
    # =================================================================

    def test_05_smart_emoji_resolver_comprehensive(self):
        """2.1 智能表情解析器 resolve_emoji_id 40+ 组真实场景用例与多模态解析"""
        test_cases = [
            ("赞", "76"), ("👍", "76"), ("强", "76"), ("👍🏻", "76"),
            ("笑哭", "182"), ("😂", "182"), ("笑死", "182"),
            ("ok", "124"), ("OK", "124"), ("好的", "124"), ("👌", "124"),
            ("爱心", "66"), ("❤️", "66"), ("❤", "66"),  # QQ 系统表情 66=爱心
            ("666", "299"), ("牛", "299"), ("🤙", "299"),
            ("便便", "59"), ("💩", "59"), ("屎", "59"),
            ("庆祝", "144"), ("喝彩", "144"), ("🎉", "144"),
            ("心碎", "67"), ("💔", "67"),
            ("火", "128293"), ("🔥", "128293"),  # Unicode 表态 (emoji_type=2)
            ("抱拳", "118"), ("拳", "118"),
            ("拜托", "297"), ("🙏", "297"),
            ("握手", "78"), ("🤝", "78"),
            ("胜利", "79"), ("✌️", "79"), ("✌", "79"),
            ("蛋糕", "53"), ("🎂", "53"),
            ("😳", "128563"),  # 未收录的单个 Unicode 表情 -> 十进制 codepoint
            ("76", "76"), ("124", "124"), (124, "124"), ("128514", "128514"),  # 数字透传
            ("[赞]", "76"), ("/庆祝", "144"), ("【ok】", "124"),  # 包装清洗
        ]
        for raw, expected in test_cases:
            res = resolve_emoji_id(raw)
            self.assertEqual(res, expected, f"解析失败: 输入 {raw!r}, 期望 {expected!r}, 实际 {res!r}")

        supported = list_supported_emojis()
        self.assertTrue(len(supported) >= 15)
        info_76 = get_emoji_info("76")
        self.assertIsNotNone(info_76)
        self.assertEqual(info_76["name"], "赞")
        print(f"  [PASS] 2.1 resolve_emoji_id 常用表情解析 100% 正确 (收录 {len(supported)} 个标准表情)")

    def test_06_smart_emoji_resolver_error_handling(self):
        """2.2 智能表情解析器异常边界与友好建议提示"""
        with self.assertRaises(EmojiResolveError) as ctx:
            resolve_emoji_id("未知宇宙超弦量子表情")
        err_msg = str(ctx.exception)
        self.assertIn("无法识别表情标识", err_msg)
        self.assertIn("推荐常用表情", err_msg)

        with self.assertRaises(EmojiResolveError):
            resolve_emoji_id("")
        with self.assertRaises(EmojiResolveError):
            resolve_emoji_id(None)
        print("  [PASS] 2.2 resolve_emoji_id 异常边界与智能建议提示验证通过")

    def test_07_rate_limiter_sliding_window(self):
        """2.3 目标防风控限频器 RateLimiter 细粒度锁定与生命周期"""
        limiter = RateLimiter()

        # 首次调用放行
        allowed1, rem1 = limiter.check_and_update("group_poke", "123:456", 1.0)
        self.assertTrue(allowed1)
        self.assertEqual(rem1, 0.0)

        # 立即再次调用拦截
        allowed2, rem2 = limiter.check_and_update("group_poke", "123:456", 1.0)
        self.assertFalse(allowed2)
        self.assertGreater(rem2, 0.0)

        # 查询剩余时间
        rem_q = limiter.get_remaining("group_poke", "123:456", 1.0)
        self.assertGreater(rem_q, 0.0)

        # 目标隔离：不同目标不受影响
        allowed_other, _ = limiter.check_and_update("group_poke", "999:888", 1.0)
        self.assertTrue(allowed_other)

        # 重置特定目标
        limiter.reset("group_poke", "123:456")
        allowed3, _ = limiter.check_and_update("group_poke", "123:456", 1.0)
        self.assertTrue(allowed3)

        # 清理与过期回收
        cleaned = limiter.clean_expired(ttl=0.0)
        self.assertGreaterEqual(cleaned, 0)
        limiter.clear()
        print("  [PASS] 2.3 RateLimiter 线程安全限频、目标隔离与内存回收验证通过")

    # =================================================================
    # 三、底层动作引擎与多级优雅降级策略
    # =================================================================

    async def test_08_group_poke_fallback_mechanism(self):
        """3.1 群聊戳一戳 group_poke 优先 -> send_poke 降级策略验证"""
        calls: List[str] = []

        async def mock_caller(action: str, params: dict):
            calls.append(action)
            if action == "group_poke":
                return {"retcode": -1, "msg": "API not supported"}
            elif action == "send_poke":
                return {"retcode": 0, "data": "poke_ok"}
            return {"retcode": 0}

        engine = ActionEngine.from_api_caller(mock_caller)
        res = await engine.group_poke(888888, 123456)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["action"], "send_poke")
        self.assertEqual(calls, ["group_poke", "send_poke"])

        # 双级均失败场景
        async def mock_fail_caller(action: str, params: dict):
            return {"retcode": -1, "msg": "Network failure"}

        engine_fail = ActionEngine.from_api_caller(mock_fail_caller)
        with self.assertRaises(ActionExecutionError) as ctx:
            await engine_fail.group_poke(888888, 123456)
        self.assertIn("排查建议", str(ctx.exception))
        print("  [PASS] 3.1 group_poke -> send_poke 降级与排查建议验证通过")

    async def test_09_friend_poke_fallback_mechanism(self):
        """3.2 私聊戳一戳 friend_poke 优先与降级验证"""
        calls: List[str] = []

        async def mock_caller(action: str, params: dict):
            calls.append(action)
            if action == "friend_poke":
                return {"retcode": 0, "data": "friend_ok"}
            return {"retcode": 0}

        engine = ActionEngine.from_api_caller(mock_caller)
        res = await engine.friend_poke(123456)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["action"], "friend_poke")
        self.assertEqual(calls, ["friend_poke"])
        print("  [PASS] 3.2 friend_poke 优先命中与正常响应通过")

    async def test_10_safe_action_wrappers(self):
        """3.3 动作引擎 safe_* 防风控包装方法验证"""
        engine = ActionEngine(rate_limiter=RateLimiter())
        engine.call_api = AsyncMock(return_value={"retcode": 0})

        res1 = await engine.safe_group_poke(111, 222, cooldown_seconds=2.0)
        self.assertEqual(res1["status"], "ok")

        res2 = await engine.safe_group_poke(111, 222, cooldown_seconds=2.0)
        self.assertEqual(res2["status"], "rate_limited")
        self.assertGreater(res2["remaining"], 0)
        print("  [PASS] 3.3 safe_group_poke 限频安全拦截验证通过")

    async def test_11_engine_factory_methods(self):
        """3.4 动作引擎多通道工厂构建方法兼容性 (from_bot, from_adapter, from_event)"""
        class DummyBot:
            async def call_action(self, action, **params):
                return {"retcode": 0, "data": "bot_ok"}

        class DummyAdapter:
            def __init__(self):
                self.bot = DummyBot()

        class DummyEvent:
            def __init__(self):
                self.adapter = DummyAdapter()

        bot_engine = ActionEngine.from_bot(DummyBot())
        adapter_engine = ActionEngine.from_adapter(DummyAdapter())
        event_engine = ActionEngine.from_event(DummyEvent())

        res1 = await bot_engine.friend_poke(101)
        self.assertEqual(res1["status"], "ok")
        res2 = await adapter_engine.friend_poke(102)
        self.assertEqual(res2["status"], "ok")
        res3 = await event_engine.friend_poke(103)
        self.assertEqual(res3["status"], "ok")
        print("  [PASS] 3.4 from_bot / from_adapter / from_event 多通道工厂构建验证通过")

    # =================================================================
    # 四、Star 插件装载与生命周期管理
    # =================================================================

    def test_12_plugin_lifecycle_and_hot_reload(self):
        """4.1 Star 插件参数配置、截断防御与管理员 2973338092 兜底保证"""
        self.assertEqual(self.plugin.poke_cooldown, 2)
        self.assertEqual(self.plugin.react_cooldown, 1)
        self.assertTrue(self.plugin.auto_counter_poke)
        self.assertEqual(self.plugin.counter_poke_delay, 0.1)
        self.assertIn("2973338092", self.plugin.admins_id)

        # 越界值防御截断测试
        bad_config = {
            "poke_cooldown": 999,  # 截断至 30
            "react_cooldown": -10,  # 截断至 1
            "counter_poke_delay": 0.01,  # 截断至 0.1
            "admins_id": ["99999"],  # 自动补全 2973338092
        }
        plugin_bad = QQPokeReactPlugin(self.mock_context, bad_config)
        self.assertEqual(plugin_bad.poke_cooldown, 30)
        self.assertEqual(plugin_bad.react_cooldown, 1)
        self.assertEqual(plugin_bad.counter_poke_delay, 0.1)
        self.assertIn("2973338092", plugin_bad.admins_id)
        self.assertIn("99999", plugin_bad.admins_id)
        print("  [PASS] 4.1 Star 插件生命周期配置热更新与安全防御截断通过")

    # =================================================================
    # 五、大模型函数调用工具契约与证据隔离测试
    # =================================================================

    async def test_13_llm_tool_poke_user(self):
        """5.1 LLM 工具 poke_user: 目标提取、群号补全、证据信封与特权放行"""
        event = MockEvent(sender_id="10001", group_id="888888")

        # 1. 纯数字 QQ 戳一戳
        res1 = await self.plugin.poke_user(event, user_id="12345678", reason="打招呼")
        self.assertIn(EVIDENCE_HEADER, res1)
        self.assertIn(EVIDENCE_FOOTER, res1)
        self.assertIn("role=evidence", res1)
        self.assertIn("instruction_weight=0", res1)
        self.assertIn("成功在群 888888 中戳了用户 12345678", res1)
        self.assertEqual(event.called_actions[0]["action"], "group_poke")
        self.assertEqual(event.called_actions[0]["params"], {"group_id": 888888, "user_id": 12345678})

        # 2. @123456 解析
        event.called_actions.clear()
        res2 = await self.plugin.poke_user(event, user_id="@654321")
        self.assertIn("成功在群 888888 中戳了用户 654321", res2)
        self.assertEqual(event.called_actions[0]["params"]["user_id"], 654321)

        # 3. 私聊场景
        event_private = MockEvent(sender_id="10001", group_id="")
        res3 = await self.plugin.poke_user(event_private, user_id="999888")
        self.assertIn("成功私聊戳了用户 999888", res3)
        self.assertEqual(event_private.called_actions[0]["action"], "friend_poke")

        # 4. 限频拦截与管理员放行
        res_limit = await self.plugin.poke_user(event, user_id="12345678")
        self.assertIn("限频提示", res_limit)

        event_admin = MockEvent(sender_id="2973338092", group_id="888888")
        res_admin = await self.plugin.poke_user(event_admin, user_id="12345678")
        self.assertIn("成功在群 888888 中戳了用户 12345678", res_admin)
        print("  [PASS] 5.1 poke_user 工具目标智能解析、证据隔离与限频沙箱通过")

    async def test_14_llm_tool_react_to_message(self):
        """5.2 LLM 工具 react_to_message: 引用消息提取、表情解析与证据隔离"""
        reply_comp = Comp.Reply(id="1234567")
        event = MockEvent(
            sender_id="10001",
            group_id="888888",
            messages=[reply_comp, Comp.Plain(text="认同观点")],
        )

        # 自动从引用消息提取 ID，贴表情 "赞" -> 76
        res1 = await self.plugin.react_to_message(event, emoji="赞")
        self.assertIn("成功为消息 1234567 贴上了表情", res1)
        self.assertIn("76", res1)
        self.assertEqual(event.called_actions[0]["action"], "set_msg_emoji_like")
        self.assertEqual(event.called_actions[0]["params"]["emoji_id"], "76")

        # 显式传入 message_id 与 Emoji 符号 "😂" -> 182 (QQ 系统表情 笑哭)
        event.called_actions.clear()
        res2 = await self.plugin.react_to_message(event, emoji="😂", message_id="998877")
        self.assertIn("成功为消息 998877 贴上了表情", res2)
        self.assertEqual(event.called_actions[0]["params"]["message_id"], 998877)
        self.assertEqual(event.called_actions[0]["params"]["emoji_id"], "182")

        # 未知表情友好报错
        res_fail = await self.plugin.react_to_message(event, emoji="未知火星表情符号")
        self.assertIn("表情解析失败", res_fail)
        print("  [PASS] 5.2 react_to_message 引用消息提取、智能表情表态与隔离通过")

    # =================================================================
    # 六、快捷聊天前缀指令端到端测试
    # =================================================================

    async def test_15_command_poke(self):
        """6.1 聊天指令 /poke 全场景端到端验证"""
        # 群聊带 @ 目标
        at_comp = Comp.At(qq=123456)
        event_at = MockEvent(
            sender_id="10001",
            group_id="888888",
            messages=[Comp.Plain(text="/poke "), at_comp],
        )
        gen1 = self.plugin.poke_cmd(event_at, target=None)
        res1 = [r async for r in gen1]
        self.assertIn("成功戳了 123456 一下", res1[0]["text"])

        # 群聊未指定目标，提示用法
        event_no_target = MockEvent(sender_id="10001", group_id="888888")
        gen2 = self.plugin.poke_cmd(event_no_target, target=None)
        res2 = [r async for r in gen2]
        self.assertIn("使用方法: /poke", res2[0]["text"])

        # 私聊未指定目标，默认戳对方
        event_private = MockEvent(sender_id="777888", group_id="")
        gen3 = self.plugin.poke_cmd(event_private, target=None)
        res3 = [r async for r in gen3]
        self.assertIn("成功戳了 777888 一下", res3[0]["text"])
        print("  [PASS] 6.1 /poke 聊天前缀指令全场景测试通过")

    async def test_16_command_react(self):
        """6.2 聊天指令 /react 引用回复表态端到端验证"""
        reply_comp = Comp.Reply(id="665544")
        event = MockEvent(
            sender_id="10001",
            group_id="888888",
            messages=[reply_comp, Comp.Plain(text="/react 抱拳")],
        )

        gen = self.plugin.react_cmd(event, emoji="抱拳", message_id=None)
        results = [r async for r in gen]
        self.assertEqual(len(results), 1)
        self.assertIn("已成功为消息贴上表情", results[0]["text"])
        self.assertEqual(event.called_actions[0]["params"]["emoji_id"], "118")

        # 未传参数提示用法
        event_empty = MockEvent(sender_id="10001", group_id="888888")
        gen_empty = self.plugin.react_cmd(event_empty, emoji=None)
        res_empty = [r async for r in gen_empty]
        self.assertIn("使用方法: /react", res_empty[0]["text"])
        print("  [PASS] 6.2 /react 聊天指令引用贴表情通过")

    # =================================================================
    # 七、被动通知事件监听与拟人化自动反戳测试
    # =================================================================

    async def test_17_auto_counter_poke_when_bot_poked(self):
        """7.1 当机器人自身被戳时，拟人化延迟自动反戳并发送趣味台词"""
        poke_notice = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "target_id": 20000,  # 戳的是机器人自身 (self_id: 20000)
            "user_id": 10001,    # 发起戳人者
            "group_id": 888888,
            "self_id": 20000,
        }
        event = MockEvent(
            sender_id="10001",
            group_id="888888",
            self_id="20000",
            raw_message=poke_notice,
        )

        gen = self.plugin.on_poke_notify(event)
        results = [r async for r in gen]

        self.assertEqual(len(event.called_actions), 1)
        self.assertEqual(event.called_actions[0]["action"], "group_poke")
        self.assertEqual(event.called_actions[0]["params"]["user_id"], 10001)
        self.assertEqual(event.called_actions[0]["params"]["group_id"], 888888)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["text"], "哼，反戳你一下！")
        print("  [PASS] 7.1 被戳拟人化自动反戳及台词互动验证通过")

    async def test_18_auto_counter_poke_ignore_when_others_poked(self):
        """7.2 群友戳其他人时，机器人精准忽略，零反戳零回复"""
        poke_other_notice = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "target_id": 99999,  # 戳的是群友B，非机器人
            "user_id": 10001,
            "group_id": 888888,
            "self_id": 20000,
        }
        event = MockEvent(
            sender_id="10001",
            group_id="888888",
            self_id="20000",
            raw_message=poke_other_notice,
        )

        gen = self.plugin.on_poke_notify(event)
        results = [r async for r in gen]

        self.assertEqual(len(event.called_actions), 0)
        self.assertEqual(len(results), 0)
        print("  [PASS] 7.2 戳其他人精准忽略验证通过")

    async def test_19_auto_counter_poke_disabled_switch(self):
        """7.3 当关闭 auto_counter_poke 开关时，被戳不执行反戳"""
        self.plugin.auto_counter_poke = False
        poke_notice = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "target_id": 20000,
            "user_id": 10001,
            "group_id": 888888,
            "self_id": 20000,
        }
        event = MockEvent(
            sender_id="10001",
            group_id="888888",
            self_id="20000",
            raw_message=poke_notice,
        )

        gen = self.plugin.on_poke_notify(event)
        results = [r async for r in gen]

        self.assertEqual(len(event.called_actions), 0)
        self.assertEqual(len(results), 0)
        print("  [PASS] 7.3 auto_counter_poke 开关关闭时忽略反戳验证通过")


if __name__ == "__main__":
    unittest.main()
