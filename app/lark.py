"""lark-cli 子进程封装。

所有调用统一走 --format json;部分命令会在 JSON 前后夹带进度行(如分页提示),
因此用 raw_decode 从 stdout 中提取第一个完整 JSON 对象,而不是整段 loads。
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from .settings import settings


class LarkError(RuntimeError):
    """lark-cli 返回 ok:false 或输出无法解析时抛出。"""

    def __init__(self, message: str, *, error: dict | None = None, stderr: str = ""):
        super().__init__(message)
        self.error = error or {}
        self.stderr = stderr

    @property
    def hint(self) -> str:
        return str(self.error.get("hint", ""))


def run(args: list[str], *, timeout: int = 120, cwd: str | None = None) -> dict:
    """执行 lark-cli 子命令并返回完整 envelope:{ok, identity, data} / {ok, error}。"""
    cmd = [settings.lark_bin, *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired as e:
        raise LarkError(f"lark-cli 超时({' '.join(args[:3])}..., {timeout}s)") from e
    except FileNotFoundError as e:
        raise LarkError(f"找不到可执行文件: {settings.lark_bin}") from e

    envelope = _extract_json(proc.stdout)
    if envelope is None:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise LarkError(f"lark-cli 输出无法解析为 JSON: {' '.join(args[:3])}...; 输出末尾: {tail}", stderr=tail)
    if not envelope.get("ok"):
        err = envelope.get("error") or {}
        raise LarkError(
            "lark-cli 失败: {type}/{subtype}: {message}".format(
                type=err.get("type", "?"), subtype=err.get("subtype", "?"), message=err.get("message", "?")
            ),
            error=err,
            stderr=(proc.stderr or "").strip()[-500:],
        )
    return envelope


def data(args: list[str], *, timeout: int = 120, cwd: str | None = None) -> Any:
    return run(args, timeout=timeout, cwd=cwd).get("data")


def run_raw(args: list[str], *, timeout: int = 60) -> dict:
    """少数命令(如 whoami)输出裸 JSON 而非标准 envelope,用这个方法取。"""
    proc = subprocess.run([settings.lark_bin, *args], capture_output=True, text=True, timeout=timeout)
    obj = _extract_json(proc.stdout)
    if obj is None:
        raise LarkError(f"lark-cli 输出无法解析: {' '.join(args)}", stderr=(proc.stderr or "")[-300:])
    return obj


def _extract_json(text: str) -> dict | None:
    idx = text.find("{")
    if idx < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[idx:])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# 领域辅助:多维表格记录、群成员、消息发送
# ---------------------------------------------------------------------------

def records_list(base_token: str, table_id: str, *, timeout: int = 120) -> list[dict]:
    """拉全表记录,归一化为 [{"record_id":..., "fields":{列名: 值}}]。"""
    out: list[dict] = []
    offset = 0
    while True:
        page = data([
            "base", "+record-list",
            "--base-token", base_token, "--table-id", table_id,
            "--format", "json", "--limit", "200", "--offset", str(offset),
        ], timeout=timeout)
        fields: list[str] = page.get("fields") or []
        rows = page.get("data") or []
        rids = page.get("record_id_list") or []
        for i, row in enumerate(rows):
            rid = rids[i] if i < len(rids) else ""
            out.append({"record_id": rid, "fields": dict(zip(fields, row))})
        total_seen = offset + len(rows)
        if not rows or total_seen >= int(page.get("total", total_seen)) or len(rows) < 200:
            break
        offset = total_seen
    return out


def group_members(chat_id: str, *, identity: str = "bot", timeout: int = 120) -> tuple[list[dict], list[dict]]:
    """返回 (users, bots),元素形如 {"member_id": "ou_..", "name": .., "app_id"?..}。"""
    # 注意:不要用 --jq 投影——加了 --jq 后 CLI 输出的是裸对象而非标准 envelope
    d = data([
        "im", "+chat-members-list", "--chat-id", chat_id,
        "--page-all", "--as", identity, "--format", "json",
    ], timeout=timeout)
    if isinstance(d, dict):
        return d.get("users") or [], d.get("bots") or []
    return [], []


def send_message(
    *,
    chat_id: str | None = None,
    user_id: str | None = None,
    identity: str = "bot",
    text: str | None = None,
    markdown: str | None = None,
    interactive: dict | None = None,
    msg_type: str | None = None,
    content: str | None = None,
    dry_run: bool = False,
    timeout: int = 60,
) -> dict:
    """发送消息。text/markdown/interactive 三选一;interactive 传 dict 自动序列化。"""
    args = ["im", "+messages-send", "--as", identity]
    if chat_id:
        args += ["--chat-id", chat_id]
    elif user_id:
        args += ["--user-id", user_id]
    else:
        raise ValueError("chat_id 或 user_id 必须提供一个")
    if interactive is not None:
        args += ["--msg-type", "interactive", "--content", json.dumps(interactive, ensure_ascii=False)]
    elif markdown is not None:
        args += ["--markdown", markdown]
    elif text is not None:
        args += ["--text", text]
    elif content is not None:
        args += ["--msg-type", msg_type or "text", "--content", content]
    else:
        raise ValueError("必须提供消息内容")
    if dry_run:
        args.append("--dry-run")
    return data(args, timeout=timeout)
