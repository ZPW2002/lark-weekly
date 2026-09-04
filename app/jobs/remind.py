"""周五 17:00:统计未提交成员并在群里 @ 催交。

提交名单来自周报记录表(表单"提交人"人员字段,自动带 open_id),
群成员名单来自 im +chat-members-list,做差集。
"""

from __future__ import annotations

import logging

from .. import lark
from ..config_store import ConfigError, ConfigStore
from ..settings import settings
from ..weekly import iso_week, now_in, week_reports

log = logging.getLogger(__name__)


def collect(cfg: dict) -> tuple[str, list[dict], list[dict], str]:
    """返回 (group_chat_id, 未提交成员列表, 全员列表, 周次)。"""
    chat_id = cfg.get("group_chat_id") or ""
    tz = cfg.get("timezone") or "Asia/Shanghai"
    week = iso_week(now_in(tz))

    # 机器人身份缺 im:chat.members:read 等 scope,成员/记录读取统一走已授权的 user 身份
    users, _bots = lark.group_members(chat_id, identity="user")
    records = lark.records_list(settings.base_token, cfg.get("reports_table_id") or "")
    reports = week_reports(records, week, tz)
    submitted = {r["open_id"] for r in reports if r["open_id"]}

    pending = [u for u in users if u.get("member_id") and u["member_id"] not in submitted]
    return chat_id, pending, users, week


def run(store: ConfigStore, *, dry_run: bool = False) -> None:
    cfg = store.get(force=True)
    title = cfg.get("weekly_title") or "周报"
    if dry_run:
        if not cfg.get("group_chat_id"):
            log.info("[dry-run] 未配置 group_chat_id,仅演示文案")
            log.info("[dry-run] 文案: 请以下同学尽快提交:%s", "<at user_id=\"ou_xxx\"></at>")
            return
    store.require(cfg, "group_chat_id", "reports_table_id")

    chat_id, pending, _users, week = collect(cfg)
    if not pending:
        md = f"🎉 本周{title}已全部提交,感谢大家的配合!"
        if dry_run:
            log.info("[dry-run] 全部已提交(%s),将发送: %s", week, md)
            return
        lark.send_message(chat_id=chat_id, identity="bot", markdown=md)
        log.info("%s 全部已提交,已发送表扬消息", week)
        return

    ats = " ".join(f'<at user_id="{u["member_id"]}"></at>' for u in pending)
    names = "、".join(u.get("name") or "" for u in pending)
    text = (
        f"⏰ {title}催交通知\n\n"
        f"以下 {len(pending)} 位同学本周还未提交{title},请于今天 18:00 前补交:"
        f"{ats}\n\n👉 提交入口:{cfg.get('form_url') or ''}"
    )
    if dry_run:
        log.info("[dry-run] %s 未提交 %d 人:%s", week, len(pending), names)
        log.info("[dry-run] 将发送文本(含 %d 个 @ 标签)", len(pending))
        return

    lark.send_message(chat_id=chat_id, identity="bot", text=text)
    log.info("%s 已催交,未提交 %d 人:%s", week, len(pending), names)
