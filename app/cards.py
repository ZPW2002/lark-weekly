"""管理员确认卡片的 JSON 构造(飞书 interactive 消息)。

飞书卡片的 lark_md 不支持 # 标题和表格,汇总 markdown 需先经 to_card_md() 转换:
标题行转加粗、表格行转普通文本。
"""

from __future__ import annotations

import json
import re

MAX_DIGEST_CHARS = 9000

_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*)$")


def to_card_md(text: str) -> str:
    out: list[str] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        is_table_row = stripped.startswith("|")
        if is_table_row and not in_table:
            out.append("**" + stripped.strip("|").replace("|", " · ").strip() + "**")
            in_table = True
            continue
        if is_table_row and in_table:
            if set(stripped) <= {"|", "-", ":", " "}:
                continue  # 表头分隔行直接丢弃
            out.append(stripped.strip("|").replace("|", " · ").strip())
            continue
        in_table = False
        m = _HEADING_RE.match(line)
        if m:
            out.append(f"**{m.group(1).strip()}**")
            continue
        out.append(line)
    return "\n".join(out)


def summary_card(week: str, digest_md: str, *, email_to: str = "", count: int = 0) -> dict:
    body = to_card_md((digest_md or "").strip()) or "(汇总内容为空)"
    if len(body) > MAX_DIGEST_CHARS:
        body = body[:MAX_DIGEST_CHARS] + "\n\n……(内容过长已截断,完整内容见邮件)"
    recipients = email_to or "(邮件收件人未配置)"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": f"📋 周报汇总 {week}(已提交 {count} 人)"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body}},
            {"tag": "hr"},
            {"tag": "note", "elements": [
                {"tag": "plain_text", "content": f"确认后将发送邮件至:{recipients}"}
            ]},
            {"tag": "action", "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "✅ 确认发送邮件"},
                    "type": "primary",
                    "value": {"action": "confirm_send", "week": week},
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔄 重新生成"},
                    "value": {"action": "regen", "week": week},
                },
            ]},
        ],
    }


def card_json(card: dict) -> str:
    return json.dumps(card, ensure_ascii=False)
