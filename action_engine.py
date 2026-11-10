# -*- coding: utf-8 -*-
"""
AstrBot 戳一戳与表情回应底层动作引擎与防风控限频器: action_engine.py
====================================================================
版本：v1.0.0
适用平台：OneBot 11 / NapCat / NTQQ 机器人架构
核心特性：
1. 直连 NTQQ / NapCat OneBot 11 扩展协议，无冗余代理开销；
2. 动作多级兼容与降级保障：
   - group_poke 优先 -> send_poke 降级 -> 失败原因与排查提示兜底；
   - friend_poke 优先 -> send_poke 降级 -> 失败原因与排查提示兜底；
   - set_msg_emoji_like 原生贴表情表态与智能解析；
3. 智能表情解析器 (Smart Emoji Resolver):
   - 支持 Unicode Emoji、30+ 常见中文别名、英文名双向映射为 NTQQ 官方数字 emoji_id；
   - 纯数字 ID 智能透传，未知表情友好建议；
4. 目标防风控限频器 (RateLimiter):
   - 线程/协程安全，以 (action_type, target_key) 细粒度锁定冷却；
   - 提供冷却检查、原子更新、冷却查询、重置清理与过期回收；
5. 多通道工厂方法抽象：
   - 支持 from_bot, from_adapter, from_event, from_api_caller, from_http。
"""

import asyncio
import logging
import threading
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("astrbot_plugin_qq_poke_react.action_engine")


# =====================================================================
# 异常体系定义
# =====================================================================

class ActionEngineError(Exception):
    """动作引擎基础异常类"""
    pass


class ActionAPIError(ActionEngineError):
    """OneBot 11 API 业务逻辑错误（例如 retcode != 0 或 status == 'failed'）"""

    def __init__(self, retcode: int, message: str, action: str):
        self.retcode = retcode
        self.message = message
        self.action = action
        super().__init__(f"OneBot API [{action}] 调用失败 (retcode={retcode}): {message}")


class ActionNetworkError(ActionEngineError):
    """网络或直连 HTTP 通讯异常"""
    pass


class ActionExecutionError(ActionEngineError):
    """动作执行最终失败（包含多级降级后的排查诊断）"""
    pass


class EmojiResolveError(ActionEngineError):
    """智能表情解析错误（包含建议支持的表情列表）"""

    def __init__(self, raw_input: str, suggestions: Optional[List[str]] = None):
        self.raw_input = raw_input
        self.suggestions = suggestions or []
        tips = f"，推荐常用表情: {', '.join(self.suggestions[:10])}" if self.suggestions else ""
        super().__init__(f"无法识别表情标识 '{raw_input}'{tips}。请传入纯数字 emoji_id、常见 Emoji 或中文名称。")


# =====================================================================
# 智能表情解析器 (Smart Emoji Resolver)
# =====================================================================

# NTQQ 官方系统表情及贴表情反应（Emoji Reaction）常用数字 ID 元数据字典
# 结构: emoji_id -> {"name": 主名称, "unicode": 代表性Unicode表情, "aliases": [别名列表]}
_NTQQ_EMOJI_METADATA: Dict[str, Dict[str, Any]] = {
    # ---- 高频主表情（QQ 系统表情 face id，emoji_type=1，NapCat 依据 id 长度<=3 自动判定）----
    "76": {"name": "赞", "unicode": "👍", "aliases": ["赞", "点赞", "强", "棒", "大拇指", "thumbsup", "like", "👍", "👍🏻", "👍🏼", "👍🏽", "👍🏾", "👍🏿"]},
    "66": {"name": "爱心", "unicode": "❤️", "aliases": ["爱心", "心", "红心", "喜欢", "爱", "heart", "love", "❤️", "❤", "💖", "💗", "💓"]},
    "124": {"name": "OK", "unicode": "👌", "aliases": ["ok", "OK", "好的", "没问题", "行", "可以", "👌", "👌🏻", "👌🏼", "👌🏽", "👌🏾", "👌🏿"]},
    "182": {"name": "笑哭", "unicode": "😂", "aliases": ["笑哭", "笑死", "破涕为笑", "joy", "😂", "😹"]},
    "299": {"name": "牛啊", "unicode": "🐮", "aliases": ["666", "6", "牛", "牛啊", "牛逼", "给力", "six", "🤙"]},
    "144": {"name": "喝彩", "unicode": "🎉", "aliases": ["喝彩", "庆祝", "恭喜", "放炮", "好耶", "tada", "party", "celebrate", "🎉", "🎊"]},
    "67": {"name": "心碎", "unicode": "💔", "aliases": ["心碎", "心裂", "伤心", "叹气", "broken_heart", "💔"]},
    "59": {"name": "便便", "unicode": "💩", "aliases": ["便便", "屎", "粑粑", "poop", "poo", "💩"]},
    "63": {"name": "玫瑰", "unicode": "🌹", "aliases": ["玫瑰", "鲜花", "花", "送花", "rose", "flower", "🌹"]},
    "99": {"name": "鼓掌", "unicode": "👏", "aliases": ["鼓掌", "拍手", "clap", "applaud", "👏", "👏🏻", "👏🏼", "👏🏽", "👏🏾", "👏🏿"]},
    "106": {"name": "委屈", "unicode": "🥺", "aliases": ["委屈", "难受", "pleading", "🥺"]},
    "32": {"name": "疑问", "unicode": "❓", "aliases": ["疑问", "问号", "迷惑", "不解", "question", "?", "？", "❓", "❔"]},
    "118": {"name": "抱拳", "unicode": "🤜", "aliases": ["抱拳", "承让", "多谢", "拳", "🤜"]},
    "297": {"name": "拜谢", "unicode": "🙏", "aliases": ["拜谢", "拜托", "祈祷", "求求", "感谢", "双手合十", "保佑", "pray", "please", "🙏"]},
    "78": {"name": "握手", "unicode": "🤝", "aliases": ["握手", "合作", "成交", "handshake", "🤝"]},
    "79": {"name": "胜利", "unicode": "✌️", "aliases": ["胜利", "耶", "剪刀手", "victory", "✌️", "✌"]},
    "53": {"name": "蛋糕", "unicode": "🎂", "aliases": ["蛋糕", "生日蛋糕", "生日快乐", "cake", "🎂"]},
    # ---- 常用系统表情 ----
    "14": {"name": "微笑", "unicode": "🙂", "aliases": ["微笑", "笑", "呵呵", "smile", "🙂"]},
    "4": {"name": "得意", "unicode": "😎", "aliases": ["得意", "得瑟", "smug"]},
    "5": {"name": "流泪", "unicode": "😢", "aliases": ["流泪", "哭", "难过", "cry", "tear", "😢"]},
    "9": {"name": "大哭", "unicode": "😭", "aliases": ["大哭", "暴哭", "呜呜", "sob"]},
    "10": {"name": "尴尬", "unicode": "😅", "aliases": ["尴尬", "awkward", "😅"]},
    "12": {"name": "调皮", "unicode": "😜", "aliases": ["调皮", "吐舌", "皮", "tongue"]},
    "16": {"name": "酷", "unicode": "🕶️", "aliases": ["酷", "帅", "墨镜", "cool", "🕶️", "🕶"]},
    "21": {"name": "可爱", "unicode": "🥰", "aliases": ["可爱", "萌", "cute", "🥰"]},
    "29": {"name": "悠闲", "unicode": "🍵", "aliases": ["悠闲", "惬意", "chill"]},
    "30": {"name": "奋斗", "unicode": "💪", "aliases": ["奋斗", "加油", "努力", "冲", "fight"]},
    "39": {"name": "再见", "unicode": "👋", "aliases": ["再见", "拜拜", "挥手", "bye", "goodbye", "👋"]},
    "49": {"name": "拥抱", "unicode": "🤗", "aliases": ["拥抱", "抱抱", "贴贴", "hug", "🤗", "🫂"]},
    "60": {"name": "咖啡", "unicode": "☕", "aliases": ["咖啡", "coffee"]},
    "74": {"name": "太阳", "unicode": "🌞", "aliases": ["太阳", "晴天", "阳光", "sun", "🌞"]},
    "75": {"name": "月亮", "unicode": "🌙", "aliases": ["月亮", "晚安", "moon", "🌙"]},
    "85": {"name": "飞吻", "unicode": "😙", "aliases": ["飞吻", "亲亲", "kiss", "😙"]},
    "101": {"name": "坏笑", "unicode": "😈", "aliases": ["坏笑", "邪魅", "evil", "😈"]},
    "111": {"name": "可怜", "unicode": "🥹", "aliases": ["可怜", "求饶", "pity", "🥹"]},
    "116": {"name": "示爱", "unicode": "😍", "aliases": ["示爱", "爱你", "😍"]},
    "120": {"name": "拳头", "unicode": "👊", "aliases": ["拳头", "打你", "捶你", "punch", "fist"]},
    "179": {"name": "doge", "unicode": "🐶", "aliases": ["doge", "狗头", "滑稽", "狗", "🐶"]},
    "264": {"name": "捂脸", "unicode": "🤦", "aliases": ["捂脸", "没眼看", "facepalm", "🤦", "🤦‍♂️", "🤦‍♀️"]},
    "270": {"name": "emm", "unicode": "😑", "aliases": ["emm", "无语", "额", "呃", "speechless", "😑"]},
    "271": {"name": "吃瓜", "unicode": "🍉", "aliases": ["吃瓜", "围观", "看戏", "瓜", "watermelon"]},
    "282": {"name": "敬礼", "unicode": "🫡", "aliases": ["敬礼", "收到", "salute", "🫡"]},
    "285": {"name": "摸鱼", "unicode": "🐟", "aliases": ["摸鱼", "划水", "偷懒", "fish", "🐟"]},
    "307": {"name": "喵喵", "unicode": "🐱", "aliases": ["喵喵", "喵", "猫", "cat", "🐱"]},
    # ---- Unicode 表态（emoji_type=2，id 为十进制 codepoint，长度>3）----
    "128293": {"name": "火", "unicode": "🔥", "aliases": ["火", "火热", "爆", "fire", "hot", "🔥"]},
    "128513": {"name": "呲牙", "unicode": "😁", "aliases": ["呲牙", "露齿笑", "grin", "😁"]},
    "128516": {"name": "开心", "unicode": "😄", "aliases": ["开心", "哈哈", "happy", "😄"]},
    "128522": {"name": "嘿嘿", "unicode": "😊", "aliases": ["嘿嘿", "害羞", "blush", "😊"]},
    "128170": {"name": "肌肉", "unicode": "💪", "aliases": ["肌肉", "强壮", "muscle", "💪"]},
    "10024": {"name": "闪光", "unicode": "✨", "aliases": ["闪光", "闪亮", "sparkles", "✨"]},
}

# 构建高效的查表字典：别名/符号/原词 -> 官方数字 emoji_id
# 遵循先到先得原则，保留先定义的经典主表情（例如 76 优先于 201 对应 👍）
_EMOJI_LOOKUP_MAP: Dict[str, str] = {}


def _register_alias(key: str, eid: str) -> None:
    if not key:
        return
    k = key.strip()
    # 注册原词与小写形式（若未被先前的权威条目占用）
    if k not in _EMOJI_LOOKUP_MAP:
        _EMOJI_LOOKUP_MAP[k] = eid
    lower_k = k.lower()
    if lower_k not in _EMOJI_LOOKUP_MAP:
        _EMOJI_LOOKUP_MAP[lower_k] = eid


for eid, meta in _NTQQ_EMOJI_METADATA.items():
    _EMOJI_LOOKUP_MAP[eid] = eid
    _register_alias(meta["name"], eid)
    if meta.get("unicode"):
        _register_alias(meta["unicode"], eid)
    for alias in meta.get("aliases", []):
        _register_alias(alias, eid)


def resolve_emoji_id(emoji_input: Union[str, int]) -> str:
    """
    智能表情解析器：将各类 Unicode Emoji、中文别名、英文别名或纯数字
    双向解析为 NTQQ / NapCat OneBot 11 规范的官方数字 emoji_id。

    :param emoji_input: 表情输入，例如 "👍", "赞", "爱心", "666", 76, "124", "[赞]"
    :return: 对应的官方数字 ID 字符串（例如 "76", "124"）
    :raises EmojiResolveError: 当无法识别且非数字时抛出，附带有效表情推荐
    """
    if emoji_input is None:
        raise EmojiResolveError("空值", list(_NTQQ_EMOJI_METADATA.keys()))

    if isinstance(emoji_input, int):
        return str(emoji_input)

    raw_str = str(emoji_input).strip()
    if not raw_str:
        raise EmojiResolveError("空字符串", list(_NTQQ_EMOJI_METADATA.keys()))

    # 规范化清理常见的包裹符号（如 [赞], /赞, :thumbsup:）
    cleaned = raw_str
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1].strip()
    elif cleaned.startswith("【") and cleaned.endswith("】"):
        cleaned = cleaned[1:-1].strip()
    elif cleaned.startswith("/") or cleaned.startswith("：") or cleaned.startswith(":"):
        cleaned = cleaned.lstrip("/：:").rstrip("：:").strip()

    # 1. 优先查映射表（支持官方已知数字 ID、中文别名如 "666"、Unicode 表情如 "👍"）
    if cleaned in _EMOJI_LOOKUP_MAP:
        return _EMOJI_LOOKUP_MAP[cleaned]

    # 小写化匹配
    lower_cleaned = cleaned.lower()
    if lower_cleaned in _EMOJI_LOOKUP_MAP:
        return _EMOJI_LOOKUP_MAP[lower_cleaned]

    # 2. 若未在内置字典中，但输入本身为纯数字（如未来或新增的 NTQQ 数字 ID），直接透传
    if cleaned.isdigit():
        return cleaned

    # 3. 去除变体选择符/肤色修饰后，若为单个 Unicode 表情，则转为十进制 codepoint
    #    （NapCat setEmojiLike: id 长度 > 3 时按 emoji_type=2 Unicode 表情处理）
    stripped = "".join(
        ch for ch in cleaned
        if ch not in ("\ufe0f", "\u200d") and not (0x1F3FB <= ord(ch) <= 0x1F3FF)
    )
    if stripped in _EMOJI_LOOKUP_MAP:
        return _EMOJI_LOOKUP_MAP[stripped]
    if len(stripped) == 1 and ord(stripped) >= 0x2600:
        return str(ord(stripped))

    # 4. 无法识别，抛出友好异常与建议列表
    sample_suggestions = [
        f"{meta['name']}({meta['unicode']})" for meta in list(_NTQQ_EMOJI_METADATA.values())[:12]
    ]
    raise EmojiResolveError(raw_str, sample_suggestions)


def get_emoji_info(emoji_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """
    反查数字 emoji_id 的主名称、代表 Unicode 与别名信息。
    """
    eid = str(emoji_id).strip()
    return _NTQQ_EMOJI_METADATA.get(eid)


def list_supported_emojis() -> List[Dict[str, Any]]:
    """
    获取当前系统内置支持的所有标准表情列表（含 ID、名称、Emoji 图标与常见别名）。
    """
    res = []
    for eid, meta in _NTQQ_EMOJI_METADATA.items():
        res.append({
            "id": eid,
            "name": meta["name"],
            "unicode": meta["unicode"],
            "aliases": meta.get("aliases", []),
        })
    return res


# =====================================================================
# 目标防风控限频器 (RateLimiter)
# =====================================================================

class RateLimiter:
    """
    目标防风控限频器。

    特性：
    1. 线程/协程安全：内置互斥锁，在异步或多线程环境下均可无锁冲突访问；
    2. 细粒度锁定：以 (action_type, target_key) 联合主键维护冷却时间戳；
    3. 抗时钟跳变：采用高精度 time.monotonic() 单调时钟；
    4. 内存友好：支持主动重置、清空与超期键自动回收（clean_expired）。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._records: Dict[Tuple[str, str], float] = {}

    def check_and_update(
        self,
        action_type: str,
        target_key: Union[str, int],
        cooldown_seconds: float
    ) -> Tuple[bool, float]:
        """
        检查并原子更新动作时间戳。

        :param action_type: 动作类型标识（例如 "group_poke", "friend_poke", "emoji_like"）
        :param target_key: 目标主体唯一标识（例如群号、QQ号或 "group_id:user_id"）
        :param cooldown_seconds: 限频冷却时间（秒）
        :return: (is_allowed, remaining_time)
                 若冷却已过或首次调用，返回 (True, 0.0) 并刷新时间戳；
                 若仍在冷却中，返回 (False, remaining_time) 且不刷新时间戳。
        """
        action = str(action_type).strip()
        target = str(target_key).strip()
        key = (action, target)
        now = time.monotonic()
        cooldown = max(0.0, float(cooldown_seconds))

        with self._lock:
            if key in self._records:
                elapsed = now - self._records[key]
                if elapsed < cooldown:
                    remaining = round(cooldown - elapsed, 2)
                    return (False, max(0.01, remaining))

            # 未记录或已过冷却期，更新时间戳并放行
            self._records[key] = now
            return (True, 0.0)

    def get_remaining(
        self,
        action_type: str,
        target_key: Union[str, int],
        cooldown_seconds: float
    ) -> float:
        """
        仅查询当前剩余冷却时间（秒），不更新记录。若无冷却或已过期返回 0.0。
        """
        action = str(action_type).strip()
        target = str(target_key).strip()
        key = (action, target)
        now = time.monotonic()
        cooldown = max(0.0, float(cooldown_seconds))

        with self._lock:
            if key in self._records:
                elapsed = now - self._records[key]
                if elapsed < cooldown:
                    return round(cooldown - elapsed, 2)
        return 0.0

    def reset(
        self,
        action_type: Optional[str] = None,
        target_key: Optional[Union[str, int]] = None
    ) -> None:
        """
        清除特定或所有冷却记录。
        - 若 action_type 和 target_key 均未指定，等同于 clear()；
        - 若指定 action_type 且指定 target_key，仅重置该特定目标；
        - 若仅指定 action_type，重置该动作类型下的所有记录。
        """
        with self._lock:
            if action_type is None and target_key is None:
                self._records.clear()
                return

            act = str(action_type).strip() if action_type is not None else None
            tgt = str(target_key).strip() if target_key is not None else None

            keys_to_del = []
            for k_act, k_tgt in self._records.keys():
                if act is not None and k_act != act:
                    continue
                if tgt is not None and k_tgt != tgt:
                    continue
                keys_to_del.append((k_act, k_tgt))

            for k in keys_to_del:
                self._records.pop(k, None)

    def clear(self) -> None:
        """清空所有冷却限频记录"""
        with self._lock:
            self._records.clear()

    def clean_expired(self, ttl: float = 3600.0) -> int:
        """
        清理超过 ttl 秒未再被访问的冷却记录，释放字典内存空间。
        :return: 被清理的记录数量
        """
        now = time.monotonic()
        with self._lock:
            keys_to_del = [k for k, ts in self._records.items() if now - ts > ttl]
            for k in keys_to_del:
                self._records.pop(k, None)
            return len(keys_to_del)


# =====================================================================
# 动作执行引擎 (ActionEngine)
# =====================================================================

class ActionEngine:
    """
    直连 NapCat / NTQQ OneBot 11 的底层动作引擎。

    包含功能：
    1. 群聊戳一戳（group_poke 降级 send_poke）；
    2. 私聊戳一戳（friend_poke 降级 send_poke）；
    3. 消息表情回应/贴表情（set_msg_emoji_like 与智能解析）；
    4. 目标防风控限频安全包装方法（safe_group_poke, safe_friend_poke, safe_set_msg_emoji_like）；
    5. 支持工厂方法构建与底层 HTTP/WebSocket 通道抽象。
    """

    def __init__(
        self,
        api_caller: Optional[Callable[[str, Dict[str, Any]], Awaitable[Any]]] = None,
        http_url: Optional[str] = None,
        http_token: Optional[str] = None,
        timeout: float = 10.0,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        """
        初始化 ActionEngine。

        :param api_caller: 异步 API 调用函数，例如 `async def call(action: str, params: dict) -> Any`
        :param http_url: NapCat HTTP 根地址（如 "http://127.0.0.1:3000"）
        :param http_token: OneBot 11 HTTP Authorization Token（可选）
        :param timeout: HTTP 请求超时时间（秒）
        :param rate_limiter: 关联的限频器（若未提供则内部初始化一个独立的 RateLimiter）
        """
        self.api_caller = api_caller
        self.http_url = http_url.rstrip("/") if http_url else None
        self.http_token = http_token
        self.timeout = timeout
        self.rate_limiter = rate_limiter or RateLimiter()
        self._http_session: Optional[Any] = None

    # -----------------------------------------------------------------
    # 多通道工厂构建方法
    # -----------------------------------------------------------------

    @classmethod
    def from_api_caller(
        cls,
        api_caller: Callable[[str, Dict[str, Any]], Awaitable[Any]],
        rate_limiter: Optional[RateLimiter] = None,
    ) -> "ActionEngine":
        """从通用的异步 API 调用函数构建动作引擎"""
        if not callable(api_caller):
            raise TypeError("api_caller 必须为可调用对象 (Callable)")
        return cls(api_caller=api_caller, rate_limiter=rate_limiter)

    @classmethod
    def from_bot(
        cls,
        bot: Any,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> "ActionEngine":
        """
        从 aiocqhttp.CQHttp 或类似 Bot 实例构建动作引擎。
        优先识别 bot.call_action 或 bot.call_api。
        """
        if hasattr(bot, "call_action"):
            async def _caller(action: str, params: dict) -> Any:
                return await bot.call_action(action, **params)
            return cls(api_caller=_caller, rate_limiter=rate_limiter)
        elif hasattr(bot, "call_api"):
            async def _caller(action: str, params: dict) -> Any:
                return await bot.call_api(action, **params)
            return cls(api_caller=_caller, rate_limiter=rate_limiter)
        else:
            raise TypeError("传入的 bot 实例不包含 call_action 或 call_api 方法")

    @classmethod
    def from_adapter(
        cls,
        adapter: Any,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> "ActionEngine":
        """
        从 AstrBot 的 AiocqhttpAdapter 或类似平台适配器构建动作引擎。
        """
        if hasattr(adapter, "bot") and adapter.bot:
            return cls.from_bot(adapter.bot, rate_limiter=rate_limiter)
        elif hasattr(adapter, "call_api"):
            async def _caller(action: str, params: dict) -> Any:
                return await adapter.call_api(action, **params)
            return cls(api_caller=_caller, rate_limiter=rate_limiter)
        raise TypeError("传入的 adapter 对象不包含有效的 bot 或 call_api 属性")

    @classmethod
    def from_event(
        cls,
        event: Any,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> "ActionEngine":
        """
        从 AstrBot 消息事件 (AiocqhttpMessageEvent / AstrMessageEvent) 构建动作引擎。
        """
        if hasattr(event, "bot") and event.bot:
            return cls.from_bot(event.bot, rate_limiter=rate_limiter)
        if hasattr(event, "adapter") and event.adapter:
            return cls.from_adapter(event.adapter, rate_limiter=rate_limiter)
        if hasattr(event, "platform") and hasattr(event.platform, "bot") and event.platform.bot:
            return cls.from_bot(event.platform.bot, rate_limiter=rate_limiter)
        raise TypeError("事件对象 (event) 未包含有效的 bot、adapter 或 platform 属性")

    @classmethod
    def from_http(
        cls,
        base_url: str,
        token: Optional[str] = None,
        timeout: float = 10.0,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> "ActionEngine":
        """
        直连 NapCat HTTP API 接口构建动作引擎（适用于独立进程或自测调试）。
        """
        return cls(
            http_url=base_url,
            http_token=token,
            timeout=timeout,
            rate_limiter=rate_limiter,
        )

    # -----------------------------------------------------------------
    # 底层通讯与响应处理
    # -----------------------------------------------------------------

    async def _get_http_session(self) -> Any:
        """获取或创建 aiohttp.ClientSession"""
        if self._http_session is None or self._http_session.closed:
            try:
                import aiohttp
                headers = {}
                if self.http_token:
                    headers["Authorization"] = f"Bearer {self.http_token}"
                self._http_session = aiohttp.ClientSession(
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                )
            except ImportError:
                raise ActionNetworkError("未安装 aiohttp 依赖，无法使用 HTTP 直连模式调用 NapCat API")
        return self._http_session

    async def close(self) -> None:
        """释放底层 HTTP 会话资源"""
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None

    async def __aenter__(self) -> "ActionEngine":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    def _unwrap_response(self, res: Any, action: str) -> Any:
        """解析并校验 OneBot 11 API 响应结构"""
        if not isinstance(res, dict):
            return res

        # 校验 retcode
        retcode = res.get("retcode")
        if retcode is not None and retcode != 0:
            msg = res.get("message") or res.get("msg") or res.get("wording") or "未知错误"
            raise ActionAPIError(int(retcode), str(msg), action)

        # 校验 status
        status = res.get("status")
        if status == "failed":
            msg = res.get("message") or res.get("msg") or res.get("wording") or "调用失败"
            raise ActionAPIError(int(retcode or -1), str(msg), action)

        # 若包含 data 键则返回 data，否则返回原始字典
        return res.get("data", res)

    async def call_api(self, action: str, **params: Any) -> Any:
        """
        统一调用 OneBot 11 API 并做严格的异常捕获与解析。
        """
        if self.api_caller:
            try:
                res = await self.api_caller(action, params)
                return self._unwrap_response(res, action)
            except ActionAPIError:
                raise
            except Exception as e:
                logger.debug(f"API 调用 [{action}] 出现异常: {e}")
                raise ActionExecutionError(f"API 调用 [{action}] 失败: {e}") from e

        elif self.http_url:
            session = await self._get_http_session()
            url = f"{self.http_url}/{action}"
            try:
                async with session.post(url, json=params) as resp:
                    if resp.status != 200:
                        raise ActionNetworkError(f"HTTP 状态码异常: {resp.status} - {await resp.text()}")
                    data = await resp.json()
                    return self._unwrap_response(data, action)
            except Exception as e:
                if isinstance(e, (ActionAPIError, ActionNetworkError)):
                    raise
                logger.debug(f"HTTP 直连 NapCat [{action}] 出现异常: {e}")
                raise ActionNetworkError(f"直连 NapCat 失败: {e}") from e

        raise ActionExecutionError("ActionEngine 未配置有效的 api_caller 或 http_url")

    # -----------------------------------------------------------------
    # 核心动作封装（带多级兼容降级策略）
    # -----------------------------------------------------------------

    async def group_poke(
        self,
        group_id: Union[int, str],
        user_id: Union[int, str],
    ) -> Dict[str, Any]:
        """
        群聊戳一戳。

        调用策略：
        1. 优先调用 OneBot 11 `group_poke`；
        2. 若 retcode 异常或方法不存在，降级尝试调用 `send_poke`；
        3. 若依然失败，抛出包含诊断建议的 `ActionExecutionError`。

        :param group_id: 目标群号
        :param user_id: 目标用户 QQ 号
        :return: 包含 status, action, data 的执行结果字典
        """
        gid = int(group_id) if str(group_id).isdigit() else group_id
        uid = int(user_id) if str(user_id).isdigit() else user_id
        params = {"group_id": gid, "user_id": uid}

        first_error: Optional[Exception] = None
        try:
            res = await self.call_api("group_poke", **params)
            return {"status": "ok", "action": "group_poke", "data": res}
        except Exception as e:
            first_error = e
            logger.warning(f"调用 group_poke 失败 ({e})，降级尝试 send_poke...")

        try:
            res = await self.call_api("send_poke", **params)
            return {"status": "ok", "action": "send_poke", "data": res}
        except Exception as e:
            logger.error(f"群聊戳一戳双级调用均失败: group_poke 失败 ({first_error}), send_poke 失败 ({e})")
            raise ActionExecutionError(
                f"群聊戳一戳执行失败 (group_id={group_id}, user_id={user_id})。\n"
                f"1. group_poke 失败原因: {first_error}\n"
                f"2. send_poke 降级失败原因: {e}\n"
                f"排查建议：请确认 NapCat / NTQQ 机器人是否具备该群权限，且 NapCat 运行正常。"
            ) from e

    async def friend_poke(
        self,
        user_id: Union[int, str],
    ) -> Dict[str, Any]:
        """
        私聊/好友戳一戳。

        调用策略：
        1. 优先调用 OneBot 11 `friend_poke`；
        2. 若失败或方法不存在，降级尝试调用 `send_poke`；
        3. 若依然失败，抛出包含诊断建议的 `ActionExecutionError`。

        :param user_id: 目标好友 QQ 号
        :return: 包含 status, action, data 的执行结果字典
        """
        uid = int(user_id) if str(user_id).isdigit() else user_id
        params = {"user_id": uid}

        first_error: Optional[Exception] = None
        try:
            res = await self.call_api("friend_poke", **params)
            return {"status": "ok", "action": "friend_poke", "data": res}
        except Exception as e:
            first_error = e
            logger.warning(f"调用 friend_poke 失败 ({e})，降级尝试 send_poke...")

        try:
            res = await self.call_api("send_poke", **params)
            return {"status": "ok", "action": "send_poke", "data": res}
        except Exception as e:
            logger.error(f"好友戳一戳双级调用均失败: friend_poke 失败 ({first_error}), send_poke 失败 ({e})")
            raise ActionExecutionError(
                f"好友戳一戳执行失败 (user_id={user_id})。\n"
                f"1. friend_poke 失败原因: {first_error}\n"
                f"2. send_poke 降级失败原因: {e}\n"
                f"排查建议：请确认该目标用户是否为机器人好友，且 NapCat 开启了私聊戳一戳支持。"
            ) from e

    async def set_msg_emoji_like(
        self,
        message_id: Union[int, str],
        emoji_id: Union[int, str],
        set: bool = True,
    ) -> Dict[str, Any]:
        """
        设置或取消消息的表情回应（贴表情 / 点赞）。

        :param message_id: 目标消息 ID
        :param emoji_id: 表情 ID、Unicode Emoji 或中文别名（如 "赞", "76", "❤️", "124"）
        :param set: True 为添加回应，False 为取消回应（默认 True）
        :return: 包含 status, action, data, emoji_id 的执行结果字典
        """
        resolved_id = resolve_emoji_id(emoji_id)
        mid = int(message_id) if str(message_id).isdigit() else message_id
        params = {
            "message_id": mid,
            "emoji_id": resolved_id,
            "set": bool(set),
        }

        try:
            res = await self.call_api("set_msg_emoji_like", **params)
            return {
                "status": "ok",
                "action": "set_msg_emoji_like",
                "emoji_id": resolved_id,
                "data": res,
            }
        except Exception as e:
            logger.error(f"设置消息表情回应失败 (message_id={message_id}, emoji_id={resolved_id}): {e}")
            raise ActionExecutionError(
                f"消息表情回应执行失败 (message_id={message_id}, emoji_id={resolved_id})。\n"
                f"失败原因: {e}\n"
                f"排查建议：请确认该消息是否存在于当前群聊中且未被撤回（贴表情通常仅支持群消息）。"
            ) from e

    # -----------------------------------------------------------------
    # 带目标防风控限频保护的安全动作封装
    # -----------------------------------------------------------------

    async def safe_group_poke(
        self,
        group_id: Union[int, str],
        user_id: Union[int, str],
        cooldown_seconds: float = 3.0,
    ) -> Dict[str, Any]:
        """
        带防风控限频的群聊戳一戳。

        :param group_id: 目标群号
        :param user_id: 目标用户 QQ 号
        :param cooldown_seconds: 限频冷却时间（秒，默认 3.0）
        :return: 若冷却中返回 status="rate_limited"，否则返回执行结果
        """
        target_key = f"{group_id}:{user_id}"
        allowed, remaining = self.rate_limiter.check_and_update(
            action_type="group_poke",
            target_key=target_key,
            cooldown_seconds=cooldown_seconds,
        )
        if not allowed:
            return {
                "status": "rate_limited",
                "action": "group_poke",
                "remaining": remaining,
                "message": f"戳一戳限频冷却中，请在 {remaining:.1f} 秒后再试",
            }

        return await self.group_poke(group_id=group_id, user_id=user_id)

    async def safe_friend_poke(
        self,
        user_id: Union[int, str],
        cooldown_seconds: float = 3.0,
    ) -> Dict[str, Any]:
        """
        带防风控限频的私聊戳一戳。

        :param user_id: 目标好友 QQ 号
        :param cooldown_seconds: 限频冷却时间（秒，默认 3.0）
        :return: 若冷却中返回 status="rate_limited"，否则返回执行结果
        """
        target_key = str(user_id)
        allowed, remaining = self.rate_limiter.check_and_update(
            action_type="friend_poke",
            target_key=target_key,
            cooldown_seconds=cooldown_seconds,
        )
        if not allowed:
            return {
                "status": "rate_limited",
                "action": "friend_poke",
                "remaining": remaining,
                "message": f"好友戳一戳限频冷却中，请在 {remaining:.1f} 秒后再试",
            }

        return await self.friend_poke(user_id=user_id)

    async def safe_set_msg_emoji_like(
        self,
        message_id: Union[int, str],
        emoji_id: Union[int, str],
        cooldown_seconds: float = 1.0,
        set: bool = True,
    ) -> Dict[str, Any]:
        """
        带防风控限频的消息表情回应。

        :param message_id: 目标消息 ID
        :param emoji_id: 表情 ID、Unicode Emoji 或中文别名
        :param cooldown_seconds: 限频冷却时间（秒，默认 1.0）
        :param set: True 为添加回应，False 为取消回应
        :return: 若冷却中返回 status="rate_limited"，否则返回执行结果
        """
        target_key = str(message_id)
        allowed, remaining = self.rate_limiter.check_and_update(
            action_type="emoji_like",
            target_key=target_key,
            cooldown_seconds=cooldown_seconds,
        )
        if not allowed:
            return {
                "status": "rate_limited",
                "action": "set_msg_emoji_like",
                "remaining": remaining,
                "message": f"表情回应限频冷却中，请在 {remaining:.1f} 秒后再试",
            }

        return await self.set_msg_emoji_like(
            message_id=message_id,
            emoji_id=emoji_id,
            set=set,
        )


# =====================================================================
# 单元自测用例集 (Self-Test Suite)
# =====================================================================
