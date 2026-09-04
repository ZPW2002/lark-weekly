"""事件路由:

- im.message.receive_v1:私聊或群里 @机器人 -> pi 智能回复(按会话保持记忆)
- card.action.trigger  :管理员点卡片按钮 -> 发送邮件 / 重新生成汇总

内置指令:/new 重置会话、/help 能力说明;其余消息交给 pi。
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from .. import lark, pi_agent
from ..config_store import ConfigStore
from ..jobs import mailer, summarize

log = logging.getLogger(__name__)

HELP_TEXT = (
    "🤖 **飞书工作助手**\n"
    "- 💬 发消息、查群聊记录\n"
    "- 📊 查周报进度(多维表格)\n"
    "- 📄 创建/查询文档\n"
    "- ✅ 创建任务\n"
    "- 📧 查邮件摘要\n\n"
    "**指令**:/new 开启新会话 · /help 查看本说明\n"
    "直接说需求就行,比如\"查一下本周周报提交情况\"或\"帮我建个任务\""
)

BUILTIN_COMMANDS = {"/new": "新会话已开始 🆕", "/reset": "新会话已开始 🆕"}


class EventHandlers:
    def __init__(self, store: ConfigStore):
        self.store = store
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="evt")
        self._seen: deque[str] = deque(maxlen=512)
        self._seen_lock = threading.Lock()
        self._bot_open_id: str | None = None
        self._epoch_lock = threading.Lock()
        self._epoch: dict[str, int] = {}  # chat_id -> 会话代数,/new 时 +1 使旧 session 失效

    # -- 工具 ----------------------------------------------------------------

    def _duplicate(self, event_id: str) -> bool:
        if not event_id:
            return False
        with self._seen_lock:
            if event_id in self._seen:
                return True
            self._seen.append(event_id)
            return False

    def bot_open_id(self) -> str:
        """解析当前应用机器人的 open_id:从配置群成员的 bots 桶里按 app_id 匹配。"""
        if self._bot_open_id:
            return self._bot_open_id
        cfg = self.store.get()
        app_id = self._app_id()
        group = cfg.get("group_chat_id") or ""
        if not app_id or not group:
            return ""
        try:
            _users, bots = lark.group_members(group, identity="user")
        except Exception:
            return ""
        for b in bots:
            if b.get("app_id") == app_id and b.get("member_id"):
                self._bot_open_id = b["member_id"]
                log.info("机器人 open_id 解析成功: %s", self._bot_open_id)
                return self._bot_open_id
        log.warning("配置群里未找到本应用机器人(app_id=%s),请确认机器人已入群", app_id)
        return ""

    def _app_id(self) -> str:
        try:
            d = lark.run_raw(["whoami", "--as", "bot"])
            return str(d.get("appId") or "")
        except Exception:
            return ""

    def _session_id(self, chat_id: str) -> str:
        with self._epoch_lock:
            epoch = self._epoch.get(chat_id, 0)
        return chat_id if epoch == 0 else f"{chat_id}-{epoch}"

    # -- IM 消息 --------------------------------------------------------------

    def handle_im_message(self, evt: dict) -> None:
        if self._duplicate(str(evt.get("event_id") or "")):
            return
        # 机器人/应用自己发的消息不处理,防止自循环
        if str(evt.get("sender_type", "")).lower() in ("app", "bot"):
            return
        chat_type = evt.get("chat_type")
        content = str(evt.get("content") or "").strip()

        if chat_type == "p2p":
            pass  # 私聊全部接管
        else:
            bot_id = self.bot_open_id()
            mentions = evt.get("mentions") or []
            mentioned = any(
                (m.get("id") or m.get("open_id")) == bot_id for m in mentions if isinstance(m, dict)
            ) if bot_id else False
            if not mentioned:
                return
            content = _strip_leading_mention(content)

        if not content:
            return

        chat_id = str(evt.get("chat_id") or "")
        lowered = content.lower()

        if lowered in BUILTIN_COMMANDS:
            self._pool.submit(self._reset_session_flow, chat_id)
            return
        if lowered == "/help":
            self._pool.submit(self._reply, chat_id, HELP_TEXT)
            return
        if lowered.startswith("/"):
            self._pool.submit(self._reply, chat_id,
                              f"未知指令 {content},可用:/new 开启新会话、/help 查看能力")
            return

        self._pool.submit(self._agent_reply_flow, evt, content)

    def _reset_session_flow(self, chat_id: str) -> None:
        with self._epoch_lock:
            self._epoch[chat_id] = self._epoch.get(chat_id, 0) + 1
        try:
            pi_agent.reset_session(chat_id)  # 清理 0 代会话文件
        except Exception:
            log.debug("清理会话文件失败(不影响重置)", exc_info=True)
        self._reply(chat_id, BUILTIN_COMMANDS["/new"])

    def _reply(self, chat_id: str, text: str) -> None:
        try:
            lark.send_message(chat_id=chat_id, identity="bot", markdown=text[:4000])
        except Exception:
            log.warning("markdown 发送失败,降级为纯文本", exc_info=True)
            try:
                lark.send_message(chat_id=chat_id, identity="bot", text=text[:4000])
            except Exception:
                log.exception("回复发送失败")

    def _agent_reply_flow(self, evt: dict, content: str) -> None:
        cfg = self.store.get()
        if str(cfg.get("agent_enabled", "true")).lower() not in ("1", "true", "yes"):
            return
        chat_id = str(evt.get("chat_id") or "")
        if not chat_id:
            return
        meta = {
            "会话类型": "私聊" if evt.get("chat_type") == "p2p" else "群聊",
            "发送人": evt.get("sender_name") or "",
            "发送人 open_id": evt.get("sender_id") or "",
        }
        try:
            reply = pi_agent.agent_reply(content, meta, cfg, session_id=self._session_id(chat_id))
        except Exception as e:
            log.exception("pi 智能回复失败")
            reply = f"抱歉,我刚才处理这条消息出了点问题({e}),请稍后再试。"
        self._reply(chat_id, reply)

    # -- 卡片按钮 --------------------------------------------------------------

    def handle_card_action(self, evt: dict) -> None:
        if self._duplicate(str(evt.get("event_id") or "")):
            return
        value = evt.get("action_value") if isinstance(evt.get("action_value"), dict) else _parse_json(
            evt.get("action_value") or evt.get("value")
        )
        if not value:
            log.debug("忽略无 value 的卡片动作: %s", str(evt)[:200])
            return
        action = str(value.get("action", ""))
        week = str(value.get("week", ""))
        operator = str(evt.get("operator_id") or evt.get("open_id") or "")

        cfg = self.store.get(force=True)
        admin = cfg.get("admin_open_id") or ""
        if admin and operator and operator != admin:
            log.warning("非管理员的卡片点击已忽略: operator=%s action=%s", operator, action)
            return

        self._pool.submit(self._dispatch_card_action, action, week, operator)

    def _dispatch_card_action(self, action: str, week: str, operator: str) -> None:
        try:
            if action == "confirm_send":
                if not week:
                    raise ValueError("按钮缺少 week 参数")
                result = mailer.run(self.store, week)
                log.info("卡片确认 -> 邮件已发送: %s", result["subject"])
            elif action == "regen":
                summarize.run(self.store, week=week or None)
                log.info("卡片确认 -> 已重新生成 %s 汇总并发送新卡片", week)
            else:
                log.info("忽略未知卡片动作: %s", action)
        except Exception as e:
            log.exception("处理卡片动作失败(action=%s)", action)
            if operator:
                try:
                    lark.send_message(user_id=operator, identity="bot",
                                      text=f"⚠️ 操作失败:{e}")
                except Exception:
                    pass


def _parse_json(v) -> dict:
    import json
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip().startswith("{"):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return {}
    return {}


def _strip_leading_mention(content: str) -> str:
    """去掉消息开头的 @某某(渲染后的 mention 文本)。"""
    import re
    return re.sub(r"^@\S+\s*", "", content).strip()
