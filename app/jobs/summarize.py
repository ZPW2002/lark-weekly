"""周五 18:00:拉取本周全部周报,用 pi(deepseek)汇总成 markdown,
以 interactive 卡片发给管理员,等待按钮确认。

卡片按钮点击后由 events.handlers 处理:确认发送 -> jobs.mailer。
"""

from __future__ import annotations

import json
import logging
import re

from .. import cards, lark, pi_agent
from ..config_store import ConfigStore
from ..settings import settings
from ..weekly import iso_week, now_in, week_reports

log = logging.getLogger(__name__)


def render_records_text(reports: list[dict]) -> str:
    blocks = []
    for i, r in enumerate(reports, 1):
        blocks.append(
            f"### 成员{i}:{r['name']}\n"
            f"【本周完成】{r['done'] or '(未填写)'}\n"
            f"【下周计划】{r['plan'] or '(未填写)'}\n"
            f"【问题与协调】{r['issues'] or '(未填写)'}"
        )
    return "\n\n".join(blocks)


def run(store: ConfigStore, *, dry_run: bool = False, week: str | None = None) -> str:
    cfg = store.get(force=True)
    tz = cfg.get("timezone") or "Asia/Shanghai"
    week = week or iso_week(now_in(tz))
    store.require(cfg, "admin_open_id", "reports_table_id")

    records = lark.records_list(settings.base_token, cfg["reports_table_id"])
    reports = week_reports(records, week, tz)
    log.info("%s 已提交周报 %d 份", week, len(reports))

    if reports:
        digest = pi_agent.summarize(render_records_text(reports), cfg)
    else:
        digest = "本周暂无周报提交。"

    digest = _strip_thinking(digest)
    state_dir = settings.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"digest-{week}.md").write_text(digest, encoding="utf-8")
    pending = {
        "week": week,
        "admin_open_id": cfg["admin_open_id"],
        "email_to": cfg.get("email_to", ""),
        "email_cc": cfg.get("email_cc", ""),
        "count": len(reports),
        "generated_at": now_in(tz).isoformat(timespec="seconds"),
        "card_message_id": "",
    }
    (state_dir / f"pending-{week}.json").write_text(
        json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    card = cards.summary_card(week, digest, email_to=cfg.get("email_to", ""), count=len(reports))
    if dry_run:
        log.info("[dry-run] 汇总完成(%s),卡片将发送给 %s:\n%s",
                 week, cfg["admin_open_id"], json.dumps(card, ensure_ascii=False)[:800])
        return digest

    resp = lark.send_message(user_id=cfg["admin_open_id"], identity="bot", interactive=card)
    message_id = _find_message_id(resp)
    if message_id:
        pending["card_message_id"] = message_id
        (state_dir / f"pending-{week}.json").write_text(
            json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    log.info("%s 汇总卡片已发送给管理员(message_id=%s)", week, message_id or "?")
    return digest


def _find_message_id(resp: dict) -> str:
    """从发送响应中提取 message_id,兼容不同嵌套层次。"""
    def hunt(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "message_id" and isinstance(v, str) and v:
                    return v
                found = hunt(v)
                if found:
                    return found
        elif isinstance(obj, list):
            for it in obj:
                found = hunt(it)
                if found:
                    return found
        return ""
    return hunt(resp.get("data", resp))


def _strip_thinking(text: str) -> str:
    """部分模型会输出 <think>…</think>,去掉后再展示。"""
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.S).strip()
