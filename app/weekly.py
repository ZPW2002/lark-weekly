"""周次与提交状态计算。"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def now_in(tzname: str) -> datetime:
    tz = ZoneInfo(tzname) if ZoneInfo and tzname else dt_timezone.utc
    return datetime.now(tz)


def iso_week(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def parse_created(v) -> datetime | None:
    """解析 created_at 单元格:ISO 字符串(带时区)或毫秒时间戳。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, tz=dt_timezone.utc)
    s = str(v).strip()
    if not s:
        return None
    if s.isdigit():
        return datetime.fromtimestamp(int(s) / 1000, tz=dt_timezone.utc)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


def user_ids(cell) -> list[dict]:
    """人员单元格 -> [{"id": "ou_..", "name": ..}],兼容多种形状。"""
    if not cell:
        return []
    items = cell if isinstance(cell, list) else [cell]
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("id"):
            out.append({"id": it["id"], "name": it.get("name") or ""})
        elif isinstance(it, str) and it.startswith("ou_"):
            out.append({"id": it, "name": ""})
    return out


def week_reports(records: list[dict], week_key: str, tzname: str) -> list[dict]:
    """从周报记录表筛出指定周次的提交,归一化为报告列表。"""
    reports = []
    for rec in records:
        f = rec["fields"]
        created = parse_created(f.get("提交时间"))
        if created is None:
            continue
        created_local = created.astimezone(ZoneInfo(tzname) if ZoneInfo and tzname else dt_timezone.utc)
        if iso_week(created_local) != week_key:
            continue
        submitter = user_ids(f.get("提交人"))
        reports.append({
            "record_id": rec["record_id"],
            "open_id": submitter[0]["id"] if submitter else "",
            "name": submitter[0]["name"] if submitter else "(未识别)",
            "done": _text(f.get("本周完成")),
            "plan": _text(f.get("下周计划")),
            "issues": _text(f.get("问题与协调")),
            "created_at": created_local,
        })
    return reports


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return "\n".join(_text(seg) for seg in v).strip()
    return str(v).strip()
