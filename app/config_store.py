"""业务配置存放在多维表格「配置表」中:表头为配置项,第一行为值。

中文表头到内部 key 的映射见 KEY_MAP;人员/群聊字段取其 id,复选框转 true/false。
服务启动与每次任务执行前都会读取;带 TTL 缓存,管理员改表后约一分钟内生效。
"""

from __future__ import annotations

import threading
import time

from . import lark
from .settings import settings

# 中文表头 -> 内部 key
KEY_MAP = {
    "管理员": "admin_open_id",
    "群聊": "group_chat_id",
    "发送到": "email_to",
    "抄送": "email_cc",
    "邮件主题前缀": "email_subject_prefix",
    "提醒时间": "notify_cron",
    "催交时间": "remind_cron",
    "汇总时间": "summarize_cron",
    "时区": "timezone",
    "智能回复": "agent_enabled",
    "模型服务商": "pi_provider",
    "模型": "pi_model",
    "周报称呼": "weekly_title",
    "表单链接": "form_url",
    "记录表ID": "reports_table_id",
}
HEADER_MAP = {v: k for k, v in KEY_MAP.items()}
# 值为 id 的列(人员/群聊字段),写回时需包成 [{"id": ...}]
ID_COLUMNS = {"admin_open_id", "group_chat_id"}

REQUIRED_KEYS = ("group_chat_id", "admin_open_id", "email_to", "form_url", "reports_table_id")


class ConfigError(RuntimeError):
    pass


class ConfigStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict[str, str] = {}
        self._loaded_at = 0.0
        self._record_id = ""  # 唯一数据行的 record_id,写回用

    # -- 读取 ---------------------------------------------------------------

    def get(self, force: bool = False) -> dict[str, str]:
        with self._lock:
            if not force and self._cache and time.time() - self._loaded_at < settings.config_cache_ttl:
                return dict(self._cache)
            cfg, rid = self._load_raw()
            self._cache = cfg
            self._loaded_at = time.time()
            self._record_id = rid
            return dict(cfg)

    def _load_raw(self) -> tuple[dict[str, str], str]:
        if not settings.base_token or not settings.config_table_id:
            raise ConfigError(
                "缺少 LARK_BASE_TOKEN / LARK_CONFIG_TABLE_ID 环境变量,"
                "请在 .env 中填写周报自动化 Base 的 token 与配置表 table_id"
            )
        records = lark.records_list(settings.base_token, settings.config_table_id)
        if not records:
            raise ConfigError("配置表没有数据行,请在第一行填写各配置项的值")
        row = records[0]
        self._record_id = row["record_id"]
        fields: list[str] = list(row["fields"].keys())
        cfg: dict[str, str] = {}
        for header in fields:
            key = KEY_MAP.get(header)
            if key:
                cfg[key] = _cell_to_text(row["fields"][header])
        return cfg, row["record_id"]

    # -- 写回 ----------------------------------------------------------------

    def set(self, key: str, value: str) -> None:
        """更新某个配置项(按表头写回唯一数据行)。"""
        self.get(force=True)  # 确保拿到 record_id 与最新结构
        header = HEADER_MAP.get(key)
        if not header:
            raise ConfigError(f"未知的配置项: {key}(可用: {', '.join(HEADER_MAP)})")
        if not self._record_id:
            raise ConfigError("配置表数据行缺失,无法写回")
        if key in ID_COLUMNS:
            cell = [{"id": value}]
        elif key == "agent_enabled":
            cell = str(value).lower() in ("1", "true", "yes", "是")
        else:
            cell = value
        payload = {"update_records": {self._record_id: {header: cell}}}
        lark.run([
            "base", "+record-batch-update",
            "--base-token", settings.base_token, "--table-id", settings.config_table_id,
            "--json", _json_dumps(payload),
        ])
        self.get(force=True)

    def require(self, cfg: dict[str, str], *keys: str) -> None:
        missing = [k for k in keys if not cfg.get(k)]
        if missing:
            raise ConfigError(
                "配置表缺少必填项: " + ", ".join(missing)
                + "。请打开周报自动化 Base 的「配置表」填写后重试。"
            )


def _cell_to_text(v) -> str:
    """单元格值 -> 配置字符串:人员/群聊取 id,复选框转 true/false,富文本拼接。"""
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        ids = [seg.get("id") for seg in v if isinstance(seg, dict) and seg.get("id")]
        if ids:
            return ",".join(ids)
        return "".join(_cell_to_text(seg) for seg in v).strip()
    if isinstance(v, dict):
        if v.get("id"):
            return str(v["id"])
        if v.get("link"):
            return str(v.get("text") or v["link"]).strip()
        return str(v.get("text") or "").strip()
    return str(v).strip()


def _json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)
