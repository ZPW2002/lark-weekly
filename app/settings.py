"""环境变量引导配置:服务只需要知道 Base 和配置表的位置,其余业务配置全部来自多维表格。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # 本地开发时自动加载项目根目录的 .env;容器内由 compose 注入
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    lark_bin: str
    pi_bin: str
    base_token: str
    config_table_id: str
    state_dir: Path
    agent_home: Path
    pi_skills_dir: Path
    config_cache_ttl: int
    agent_timeout: int
    summarize_timeout: int
    mail_timeout: int
    log_level: str


def load() -> Settings:
    state_dir = Path(_env("STATE_DIR", str(Path(__file__).resolve().parent.parent / "state")))
    return Settings(
        lark_bin=_env("LARK_BIN", "lark-cli"),
        pi_bin=_env("PI_BIN", "pi"),
        base_token=_env("LARK_BASE_TOKEN"),
        config_table_id=_env("LARK_CONFIG_TABLE_ID"),
        state_dir=state_dir,
        agent_home=Path(_env("AGENT_HOME", str(Path(__file__).resolve().parent.parent / "agent_home"))),
        pi_skills_dir=Path(_env("PI_SKILLS_DIR", str(Path.home() / ".pi/agent/skills"))),
        config_cache_ttl=int(_env("CONFIG_CACHE_TTL", "60")),
        agent_timeout=int(_env("AGENT_TIMEOUT", "240")),
        summarize_timeout=int(_env("SUMMARIZE_TIMEOUT", "300")),
        mail_timeout=int(_env("MAIL_TIMEOUT", "120")),
        log_level=_env("LOG_LEVEL", "INFO"),
    )


settings = load()
