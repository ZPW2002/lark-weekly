"""周五 10:00:在目标群发送周报填写提醒(附表单链接)。"""

from __future__ import annotations

import logging

from .. import lark
from ..config_store import ConfigStore

log = logging.getLogger(__name__)


def compose(cfg: dict) -> tuple[str, str]:
    title = cfg.get("weekly_title") or "周报"
    form = cfg.get("form_url") or "(表单链接未配置)"
    md = (
        f"# 📋 {title}填写提醒\n\n"
        f"各位好,请在本周五 **17:00** 前提交本周{title}:\n\n"
        f"👉 [点击填写{title}]({form})\n\n"
        "内容包括:**本周完成 · 下周计划 · 问题与协调**。谢谢配合!"
    )
    return cfg.get("group_chat_id") or "", md


def run(store: ConfigStore, *, dry_run: bool = False) -> None:
    cfg = store.get(force=True)
    chat_id, md = compose(cfg)
    if dry_run:
        log.info("[dry-run] 发送目标: %s\n%s", chat_id or "<未配置 group_chat_id>", md)
        return
    store.require(cfg, "group_chat_id")
    lark.send_message(chat_id=chat_id, identity="bot", markdown=md)
    log.info("已发送周报填写提醒到群 %s", chat_id)
