# -*- coding: utf-8 -*-
r"""椰奶群管的纯逻辑单元测试。

运行方式（使用 AstrBot 自带 Python）：
    # 1) 插件目录必须叫 astrbot_plugin_yenai_group_admin，把它的父目录加入 PYTHONPATH
    # 2) 把 AstrBot 的 app 目录（里面是 astrbot/ 包）加入 PYTHONPATH
    set PYTHONPATH=<插件所在目录的父目录>;<AstrBot 的 app 目录>
    python -m pytest tests -q      # 或直接 python tests/test_yenai_group_admin.py

AstrBot 的 app 目录也可以用环境变量 ASTRBOT_APP_PATH 指定，免去手工拼 PYTHONPATH。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# 插件目录（astrbot_plugin_yenai_group_admin）的父目录，使其可作为包被导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# AstrBot 的 app 目录：优先环境变量，避免把某台机器的绝对路径写死在仓库里
_astrbot_app_path = os.environ.get("ASTRBOT_APP_PATH", "").strip()
if _astrbot_app_path and Path(_astrbot_app_path).is_dir():
    sys.path.insert(0, _astrbot_app_path)

# 导入 astrbot 时会按 ASTRBOT_ROOT（未设置则取当前工作目录）初始化 data/ 等目录，
# 默认指向临时目录，免得把 data/ 建在插件目录里污染仓库
os.environ.setdefault("ASTRBOT_ROOT", tempfile.mkdtemp(prefix="astrbot-test-root-"))

from astrbot_plugin_yenai_group_admin.yenaigroup.admin import parse_mute_args  # noqa: E402
from astrbot_plugin_yenai_group_admin.yenaigroup.bannedwords import (  # noqa: E402
    BannedWords,
    ReplyError,
)
from astrbot_plugin_yenai_group_admin.yenaigroup.permission import Permission  # noqa: E402
from astrbot_plugin_yenai_group_admin.yenaigroup.store import GroupStore  # noqa: E402
from astrbot_plugin_yenai_group_admin.yenaigroup.tasks import (  # noqa: E402
    build_trigger,
    hhmm_to_cron,
    normalize_cron,
)
from astrbot_plugin_yenai_group_admin.yenaigroup.utils import (  # noqa: E402
    clean_at_tokens,
    format_duration,
    has_wake_prefix,
    parse_time_arg,
    translate_china_num,
    unit_multiplier,
)
from astrbot_plugin_yenai_group_admin.yenaigroup.verify import (  # noqa: E402
    LETTER_CHARS,
    LETTER_LENGTH,
    VerifyManager,
)
from astrbot_plugin_yenai_group_admin.yenaigroup.vote import VoteManager  # noqa: E402


class FakeContext:
    def __init__(self, admins=None):
        self._admins = admins or []

    def get_config(self):
        return {"admins_id": self._admins, "wake_prefix": ["-"]}


class FakePlugin:
    def __init__(self, config=None, admins=None):
        self.config = config or {}
        self.context = FakeContext(admins)

    def conf(self, key, default=None):
        value = self.config.get(key, default)
        return default if value is None else value


# ---------------- 中文数字 / 时间 ----------------

def test_translate_china_num():
    assert translate_china_num("1") == 1
    assert translate_china_num("0") == 0
    assert translate_china_num("两") == 2
    assert translate_china_num("十") == 10
    assert translate_china_num("十一") == 11
    assert translate_china_num("一百三十二") == 132
    assert translate_china_num("三千") == 3000
    assert translate_china_num("一万零三") == 10003


def test_unit_multiplier_and_parse_time():
    assert unit_multiplier("秒") == 1
    assert unit_multiplier("S") == 1
    assert unit_multiplier("分钟") == 60
    assert unit_multiplier("分") == 60
    assert unit_multiplier("时") == 3600
    assert unit_multiplier("天") == 86400
    assert unit_multiplier("3") == 3
    assert unit_multiplier("未知单位") == 60
    assert parse_time_arg("5", "分") == 300
    assert parse_time_arg("1", "时") == 3600
    assert parse_time_arg("两", "天") == 172800
    assert parse_time_arg("5", None) == 5
    assert parse_time_arg(None, "分") is None


def test_format_duration():
    assert format_duration(3665) == "01小时01分05秒"
    assert format_duration(60) == "01分"
    assert format_duration(86400) == "1天"
    assert format_duration(0) == ""


def test_clean_at_tokens():
    assert clean_at_tokens("禁言 @张三(12345)  5分") == "禁言 5分"
    assert clean_at_tokens("禁言@李四(67890)1时") == "禁言 1时"


class FakeEvent:
    def __init__(self, segments, self_id="999"):
        self._segments = segments
        self._self_id = self_id

    def get_messages(self):
        return self._segments

    def get_self_id(self):
        return self._self_id


def test_has_wake_prefix():
    from astrbot.api.message_components import At, Plain

    assert has_wake_prefix(FakeEvent([Plain(text="-禁言 @a")])) is True
    assert has_wake_prefix(FakeEvent([Plain(text="禁言 @a")])) is False
    # 先 @ 机器人，再跟触发词
    assert has_wake_prefix(FakeEvent([At(qq="999"), Plain(text="-踢 @a")])) is True
    # @ 的是别人，不算触发词
    assert has_wake_prefix(FakeEvent([At(qq="111"), Plain(text="-踢 @a")])) is False
    # 前面有普通文本
    assert has_wake_prefix(FakeEvent([Plain(text="看看 "), Plain(text="-踢")])) is False


def test_parse_mute_args():
    assert parse_mute_args("123456 5分", False) == ("123456", "5", "分")
    assert parse_mute_args("123456", False) == ("123456", None, None)
    assert parse_mute_args("5分", True) == (None, "5", "分")
    assert parse_mute_args("", True) == (None, None, None)


# ---------------- cron ----------------

def test_normalize_and_build_cron():
    assert normalize_cron("0 0 8 * * ?") == "0 0 8 * * *"
    assert normalize_cron("0 8 * * *") == "0 8 * * *"
    assert build_trigger("0 0 8 * * ?") is not None
    assert build_trigger("0 8 * * *") is not None
    assert hhmm_to_cron("8", "5") == "0 5 8 * * ?"
    try:
        build_trigger("只有两段 错误")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("非法 cron 表达式应当抛 ValueError")


# ---------------- 权限 ----------------

def test_permission_master_and_group_lists():
    plugin = FakePlugin(
        {
            "master_qq": ["22222"],
            "white_qq": ["33333"],
            "black_qq": ["44444"],
            "group_white_list": [],
            "group_black_list": ["99999"],
        },
        admins=["11111", "Firefly"],
    )
    perm = Permission(plugin)
    assert perm.is_master("11111")       # 全局管理员里的纯数字 QQ
    assert perm.is_master("22222")       # 插件配置补充
    assert not perm.is_master("Firefly")  # 非数字标识不算主人
    assert perm.is_white("33333")
    assert perm.is_black("44444")
    assert perm.group_allowed("12345")   # 白名单留空=全部生效
    assert not perm.group_allowed("99999")  # 黑名单优先


def test_permission_group_white_list():
    plugin = FakePlugin({"group_white_list": ["12345"]})
    perm = Permission(plugin)
    assert perm.group_allowed("12345")
    assert not perm.group_allowed("54321")


# ---------------- 违禁词 ----------------

def test_banned_words_match_modes():
    with tempfile.TemporaryDirectory() as tmp:
        store = GroupStore(Path(tmp))
        plugin = FakePlugin()
        banned = BannedWords(store, plugin)
        gid = "12345"

        banned.add(gid, "广告", "精确", "禁", "1")
        banned.add(gid, "骗子", "模糊", "踢", "1")
        banned.add(gid, r"\d{6}", "正则1", "踢撤", "1")

        assert banned.match(gid, "广告") is not None
        assert banned.match(gid, "这是广告") is None
        assert banned.match(gid, "大骗子在此") is not None
        assert banned.match(gid, "编号123456") is not None
        assert banned.match(gid, "无关内容") is None

        item = banned.match(gid, "广告")
        assert item["penaltyType"] == 2  # 禁
        assert item["rawItem"] == "广告"

        try:
            banned.add(gid, "广告")
        except ReplyError:
            pass
        else:  # pragma: no cover
            raise AssertionError("重复添加违禁词应当报错")

        banned.delete(gid, "广告")
        assert banned.match(gid, "广告") is None


def test_banned_words_mute_time_and_title_words():
    with tempfile.TemporaryDirectory() as tmp:
        store = GroupStore(Path(tmp))
        banned = BannedWords(store, FakePlugin())
        gid = "12345"
        assert banned.mute_time(gid) == 300
        banned.set_mute_time(gid, 60)
        assert banned.mute_time(gid) == 60
        banned.add_title_words(gid, ["广告", "引流"])
        assert banned.title_words(gid) == ["广告", "引流"]
        banned.del_title_words(gid, ["广告"])
        assert banned.title_words(gid) == ["引流"]
        assert banned.toggle_title_filter(gid) is True
        assert banned.title_filter_exact(gid) is True


# ---------------- 投票 ----------------

def test_vote_flow():
    votes = VoteManager()
    gid, target, user, other = "12345", "222", "111", "333"
    assert votes.create(gid, target, "Ban", user) is True
    assert votes.create(gid, target, "Ban", user) is False
    assert votes.follow(gid, target, user, True)[0] == "repeated"
    assert votes.follow(gid, target, other, True)[0] == "ok"
    state = votes.get(gid, target)
    assert state.support == 2 and state.oppose == 0
    success, state = votes.settle(gid, target, 2)
    assert success is True and state.support == 2
    assert votes.get(gid, target) is None


def test_vote_fail_and_missing():
    votes = VoteManager()
    assert votes.follow("1", "2", "3", True)[0] == "no_vote"
    votes.create("1", "2", "Kick", "3")
    votes.follow("1", "2", "4", False)
    success, state = votes.settle("1", "2", 4)
    assert success is False and state.oppose == 1


# ---------------- 入群验证 ----------------

def test_verify_exact_and_fuzzy():
    manager = VerifyManager()
    session = manager.create("1", "2", 3, 10, 20)
    assert session.code == str(
        session.m - session.n if session.operator == "-" else session.m + session.n
    )
    assert manager.check("1", "2", session.code, "精确")[0] is True
    assert manager.check("1", "2", f"答案是{session.code}吧", "精确")[0] is False
    assert manager.check("1", "2", f"答案是{session.code}吧", "模糊")[0] is True
    assert manager.consume_failure("1", "2") == 2
    assert manager.consume_failure("1", "2") == 1
    assert manager.drop("1", "2") is not None
    assert manager.find("1", "2") is None


def test_verify_range_normalized():
    manager = VerifyManager()
    session = manager.create("1", "2", 1, 100, 10)  # 范围写反也要能用
    assert 10 <= session.m <= 100
    assert 10 <= session.n <= 100


# ---------------- 入群验证的“是否触发”门槛 ----------------
# 真实踩过的坑：验证原本按群默认关闭，没配置过的群新成员进群毫无反应，
# 很容易被误判成字母验证码模式坏了。现在是「默认全开 + 黑名单排除」。

class _FakeStore:
    def __init__(self, groups):
        # groups: {群号: 群配置字典}；不在字典里 = 没有配置文件
        self._groups = {str(k): dict(v) for k, v in groups.items()}

    def exists(self, group_id):
        return str(group_id) in self._groups

    def get(self, group_id):
        return self._groups.setdefault(str(group_id), {})


class _FakePerm:
    def is_black(self, user_id):
        return False

    def is_master(self, user_id):
        return False

    def is_white(self, user_id):
        return False


class _FakeManageOneBot:
    """只用到 send_private 的假 OneBot。"""

    sent = []

    def __init__(self, event):
        pass

    async def send_private(self, user_id, message, group_id=None):
        _FakeManageOneBot.sent.append((user_id, group_id, message))


class _FakeVerifyPlugin:
    def __init__(self, config, groups=None):
        self.config = config
        self.store = _FakeStore(groups or {})
        self.perm = _FakePerm()
        self.verify = VerifyManager()
        self.group_msgs = []
        self.tasks = []

    def conf(self, key, default=None):
        value = self.config.get(key, default)
        return default if value is None else value

    def spawn(self, coro):
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task

    async def send_group(self, event, text, at=None):
        self.group_msgs.append(text)


class _FakeIncreaseEvent:
    def get_self_id(self):
        return "999"


def _run_increase(plugin, group_id="658389645", user_id="10001"):
    """跑一次入群处理，返回是否真的发起了验证。"""
    from astrbot_plugin_yenai_group_admin.yenaigroup import events

    async def run():
        original = events.OneBot
        events.OneBot = _FakeManageOneBot
        _FakeManageOneBot.sent = []
        try:
            await events._on_increase(plugin, _FakeIncreaseEvent(), group_id, user_id)
            session = plugin.verify.find(group_id, user_id)
            return session, list(_FakeManageOneBot.sent), list(plugin.group_msgs)
        finally:
            events.OneBot = original
            for task in plugin.tasks:
                task.cancel()

    return asyncio.run(run())


def test_verify_default_on_for_unconfigured_group():
    """默认全开：没有配置文件的群也要验证（这正是之前的坑）。"""
    plugin = _FakeVerifyPlugin(
        {"verify_type": "字母验证码", "verify_delay": 0, "verify_time": 300},
    )
    session, private, group_msgs = _run_increase(plugin)
    assert session is not None and session.kind == "letter"
    # 验证码必须私聊下发，且不能出现在群消息里
    assert private and session.code in private[-1][2]
    assert all(session.code not in msg for msg in group_msgs)


def test_verify_group_black_list_excludes_group():
    """黑名单里的群永不验证（优先级最高）。"""
    plugin = _FakeVerifyPlugin(
        {
            "verify_type": "字母验证码",
            "verify_delay": 0,
            "verify_group_black_list": ["658389645"],
        },
    )
    session, private, group_msgs = _run_increase(plugin)
    assert session is None
    assert private == [] and group_msgs == []


def test_verify_per_group_opt_out():
    """-关闭验证 写入 verifyDisabled 后，该群不再验证。"""
    plugin = _FakeVerifyPlugin(
        {"verify_type": "字母验证码", "verify_delay": 0},
        groups={"658389645": {"verifyDisabled": True, "verifyEnabled": False}},
    )
    session, private, group_msgs = _run_increase(plugin)
    assert session is None
    assert private == [] and group_msgs == []


def test_verify_global_switch_can_turn_everything_off():
    plugin = _FakeVerifyPlugin(
        {
            "verify_type": "字母验证码",
            "verify_delay": 0,
            "verify_enabled_default": False,
        },
    )
    session, private, group_msgs = _run_increase(plugin)
    assert session is None
    assert private == [] and group_msgs == []


def test_verify_enabled_reports_reason():
    from astrbot_plugin_yenai_group_admin.yenaigroup.events import verify_enabled

    plugin = _FakeVerifyPlugin(
        {"verify_group_black_list": ["111"], "verify_enabled_default": True},
        groups={"222": {"verifyDisabled": True}, "333": {"verifyDisabled": False}},
    )
    assert verify_enabled(plugin, "111")[0] is False
    assert "黑名单" in verify_enabled(plugin, "111")[1]
    assert verify_enabled(plugin, "222")[0] is False
    assert verify_enabled(plugin, "333")[0] is True
    assert verify_enabled(plugin, "444")[0] is True  # 没有配置文件 -> 跟随默认


def test_verify_letter_falls_back_to_math_when_private_fails():
    """私聊发不出去时回退算式，并在群消息里说明原因。"""
    from astrbot_plugin_yenai_group_admin.yenaigroup import events
    from astrbot_plugin_yenai_group_admin.yenaigroup.onebot import OneBotError

    class FailingOneBot(_FakeManageOneBot):
        async def send_private(self, user_id, message, group_id=None):
            raise OneBotError("发送失败，请先添加对方为好友")

    async def run():
        original = events.OneBot
        events.OneBot = FailingOneBot
        try:
            plugin = _FakeVerifyPlugin(
                {"verify_type": "字母验证码", "verify_delay": 0, "verify_time": 300},
            )
            await events._on_increase(plugin, _FakeIncreaseEvent(), "658389645", "10001")
            session = plugin.verify.find("658389645", "10001")
            assert session is not None and session.kind == "math"
            assert plugin.group_msgs
            assert "私聊发送失败" in plugin.group_msgs[-1]
            assert session.question in plugin.group_msgs[-1]
        finally:
            events.OneBot = original
            for task in plugin.tasks:
                task.cancel()

    asyncio.run(run())


def test_verify_letter_code_charset():
    assert LETTER_LENGTH == 5
    # 不算字母 l/I、o/O，避免用户看错
    assert not set("lIoO") & set(LETTER_CHARS)
    assert len(LETTER_CHARS) == 48
    manager = VerifyManager()
    codes = [manager.create("1", "2", 1, 10, 20, kind="letter").code for _ in range(200)]
    assert all(len(code) == LETTER_LENGTH for code in codes)
    assert all(set(code) <= set(LETTER_CHARS) for code in codes)


def test_verify_letter_code_matching():
    manager = VerifyManager()
    session = manager.create("1", "2", 3, 10, 20, kind="letter")
    assert session.kind == "letter"
    assert session.question == session.code
    # 字母验证码不区分大小写（用户输入法容易自动大写）
    assert manager.check("1", "2", session.code, "精确")[0] is True
    assert manager.check("1", "2", session.code.upper(), "精确")[0] is True
    assert manager.check("1", "2", session.code.lower(), "精确")[0] is True
    assert manager.check("1", "2", f" {session.code} ", "精确")[0] is True
    assert manager.check("1", "2", f"答案是{session.code}吧", "精确")[0] is False
    assert manager.check("1", "2", f"答案是{session.code.upper()}吧", "模糊")[0] is True
    assert manager.check("1", "2", "AB", "精确")[0] is False
    assert manager.drop("1", "2") is session
    assert manager.find("1", "2") is None


def test_verify_math_kind_unchanged():
    # 新增 kind 字段后算式模式的默认行为必须保持不变
    manager = VerifyManager()
    session = manager.create("1", "2", 3, 10, 20)
    assert session.kind == "math"
    assert session.question == f"{session.m} {session.operator} {session.n}"
    assert manager.check("1", "2", session.code, "精确")[0] is True


if __name__ == "__main__":
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {exc!r}")
    print("FAILED" if failures else "ALL TESTS PASSED")
    sys.exit(1 if failures else 0)
