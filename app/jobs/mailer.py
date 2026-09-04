"""管理员确认后,把周报汇总通过飞书邮箱发送给指定收件人。

邮件以登录用户(user 身份)的飞书邮箱发出;mail +send 默认存草稿,
必须 --confirm-send 才会真正发送。
"""

from __future__ import annotations

import json
import logging
import time

import markdown as md_lib

from .. import lark
from ..config_store import ConfigStore
from ..settings import settings

log = logging.getLogger(__name__)

_HTML_TMPL = """<!DOCTYPE html>
<html><body style="font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;
font-size:14px;line-height:1.7;color:#1f2329;max-width:760px;margin:0 auto;padding:16px;">
{body}
<hr style="border:none;border-top:1px solid #e5e6eb;margin:24px 0 8px;">
<p style="color:#86909c;font-size:12px;">本邮件由周报自动化服务发送 · 生成于 {ts}</p>
</body></html>"""


def sender_address() -> str:
    """查询当前用户的飞书邮箱主地址;未开通邮箱时返回空串。"""
    try:
        d = lark.data(["mail", "user_mailboxes", "profile",
                       "--user-mailbox-id", "me", "--as", "user"])
    except lark.LarkError:
        return ""
    if isinstance(d, dict):
        return str(d.get("primary_email_address") or "")
    return ""


def run(store: ConfigStore, week: str, *, dry_run: bool = False) -> dict:
    cfg = store.get(force=True)
    state_dir = settings.state_dir
    digest_path = state_dir / f"digest-{week}.md"
    pending_path = state_dir / f"pending-{week}.json"
    if not digest_path.exists() or not pending_path.exists():
        raise FileNotFoundError(f"找不到 {week} 的汇总文件,请先执行 summarize 生成")

    digest = digest_path.read_text(encoding="utf-8")
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    email_to = [x.strip() for x in (cfg.get("email_to") or pending.get("email_to") or "").split(",") if x.strip()]
    email_cc = [x.strip() for x in (cfg.get("email_cc") or pending.get("email_cc") or "").split(",") if x.strip()]
    if not email_to and dry_run:
        email_to = ["<配置表 email_to 未填>"]
    if not email_to:
        raise ValueError("email_to 未配置,请先在配置表填写收件邮箱")

    sender = sender_address()
    if not sender:
        raise ValueError(
            "发件邮箱不可用:当前飞书账号未开通飞书邮箱(邮箱地址未分配)。"
            "请在飞书管理后台开通邮箱后重试;或在配置表中增加 SMTP 配置改用 SMTP 发送。"
        )

    prefix = cfg.get("email_subject_prefix") or "团队周报汇总"
    subject = f"{prefix}({week},已提交 {pending.get('count', '?')} 人)"
    html_file = state_dir / f"digest-{week}.html"
    body_html = _HTML_TMPL.format(
        body=md_lib.markdown(digest, extensions=["tables", "fenced_code", "nl2br"]),
        ts=time.strftime("%Y-%m-%d %H:%M %Z"),
    )
    html_file.write_text(body_html, encoding="utf-8")

    args = ["mail", "+send", "--as", "user", "--confirm-send",
            "--from", sender,
            "--to", email_to[0],
            "--subject", subject,
            "--body-file", html_file.name]
    for extra in email_to[1:]:
        args += ["--to", extra]
    for cc in email_cc:
        args += ["--cc", cc]

    if dry_run:
        log.info("[dry-run] 邮件主题: %s", subject)
        log.info("[dry-run] 收件人: to=%s cc=%s", email_to, email_cc)
        log.info("[dry-run] 命令: %s", " ".join(args[:8]) + " ...")
        return {"subject": subject, "to": email_to, "cc": email_cc, "sent": False}

    resp = lark.run(args, timeout=settings.mail_timeout, cwd=str(state_dir))
    log.info("%s 周报汇总邮件已发送: to=%s cc=%s", week, email_to, email_cc)

    # 通知管理员(卡片 token 30 分钟过期,这里直接用新消息反馈结果)
    try:
        admin = cfg.get("admin_open_id") or pending.get("admin_open_id")
        if admin:
            lark.send_message(
                user_id=admin, identity="bot",
                text=f"✅ 周报汇总({week})已确认,邮件发送至:{', '.join(email_to)}",
            )
    except Exception:  # 反馈消息失败不影响主流程
        log.warning("向管理员发送确认回执失败", exc_info=True)

    return {"subject": subject, "to": email_to, "cc": email_cc, "sent": True,
            "response": resp.get("data")}
