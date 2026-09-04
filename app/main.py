"""服务入口:APScheduler 定时任务 + 事件消费常驻进程。

配置(群/管理员/邮箱/cron)全部来自多维表格配置表,每分钟自动重载;
cron 表达式在表里修改后无需重启即可生效(时区变更除外)。
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config_store import ConfigStore
from .events.consumer import EventConsumer
from .events.handlers import EventHandlers
from .jobs import notify, remind, summarize
from .settings import settings

log = logging.getLogger(__name__)

JOB_MODULES = {"notify": notify, "remind": remind, "summarize": summarize}
CRON_KEYS = {"notify": "notify_cron", "remind": "remind_cron", "summarize": "summarize_cron"}


class Service:
    def __init__(self):
        self.store = ConfigStore()
        self.handlers = EventHandlers(self.store)
        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self.consumers: list[EventConsumer] = []
        self._stop = threading.Event()
        self._cron_snapshot: dict[str, str] = {}

    # -- 配置 ---------------------------------------------------------------

    def _load_config_with_retry(self) -> dict:
        for attempt in range(1, 11):
            try:
                return self.store.get(force=True)
            except Exception as e:
                log.warning("读取配置失败(%d/10): %s", attempt, e)
                if attempt == 10:
                    raise
                time.sleep(15)
        return {}

    # -- 调度 ----------------------------------------------------------------

    def _schedule_jobs(self, cfg: dict) -> None:
        tz = cfg.get("timezone") or "Asia/Shanghai"
        for name, module in JOB_MODULES.items():
            expr = cfg.get(CRON_KEYS[name]) or ""
            if not expr:
                log.warning("配置项 %s 为空,%s 任务未排期", CRON_KEYS[name], name)
                continue
            try:
                trigger = CronTrigger.from_crontab(expr, timezone=tz)
            except ValueError as e:
                log.error("cron 表达式非法(%s=%s): %s", CRON_KEYS[name], expr, e)
                continue
            job_id = f"job-{name}"
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            self.scheduler.add_job(
                self._run_job, trigger, args=[name], id=job_id,
                max_instances=1, coalesce=True, misfire_grace_time=3600,
            )
            log.info("已排期 %s: %s (%s)", name, expr, tz)
        self._cron_snapshot = {
            name: cfg.get(CRON_KEYS[name], "") for name in JOB_MODULES
        } | {"__tz": tz}

    def _run_job(self, name: str) -> None:
        module = JOB_MODULES[name]
        log.info("=== 定时任务 %s 开始 ===", name)
        try:
            cfg = self.store.get(force=True)  # 任务执行前强制读最新配置
            module.run(self.store, dry_run=False)
        except Exception as e:
            log.exception("定时任务 %s 失败: %s", name, e)
        log.info("=== 定时任务 %s 结束 ===", name)

    def _refresh_config_loop(self) -> None:
        """每分钟重载配置;cron 变化时自动重排(时区变化需重启生效)。"""
        try:
            cfg = self.store.get(force=True)
        except Exception:
            log.warning("配置刷新失败,沿用上次配置", exc_info=True)
            return
        snapshot = {name: cfg.get(CRON_KEYS[name], "") for name in JOB_MODULES}
        if snapshot != {k: v for k, v in self._cron_snapshot.items() if k != "__tz"}:
            log.info("检测到 cron 配置变化,重新排期: %s", snapshot)
            self._schedule_jobs(cfg)

    # -- 事件 -----------------------------------------------------------------

    def _start_consumers(self) -> None:
        self.consumers = [
            EventConsumer("im.message.receive_v1", self.handlers.handle_im_message),
            EventConsumer("card.action.trigger", self.handlers.handle_card_action),
        ]
        for c in self.consumers:
            c.start()

    # -- 启停 ------------------------------------------------------------------

    def run(self) -> None:
        logging.basicConfig(
            level=getattr(logging, settings.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        log.info("周报自动化服务启动(base=%s, 配置表=%s)", settings.base_token, settings.config_table_id)

        cfg = self._load_config_with_retry()
        for key in ("group_chat_id", "admin_open_id", "email_to"):
            if not cfg.get(key):
                log.warning("配置表「%s」尚未填写,相关功能将在填写后自动生效", key)

        settings.state_dir.mkdir(parents=True, exist_ok=True)

        self._schedule_jobs(cfg)
        self.scheduler.add_job(
            self._refresh_config_loop, IntervalTrigger(seconds=60), id="config-refresh",
            max_instances=1,
        )
        self.scheduler.start()
        self._start_consumers()

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        log.info("服务就绪,等待定时任务与飞书事件…")
        while not self._stop.is_set():
            self._stop.wait(5)

        log.info("正在关闭…")
        self.scheduler.shutdown(wait=False)
        for c in self.consumers:
            c.stop()
        log.info("已退出")

    def _handle_signal(self, signum, _frame) -> None:
        log.info("收到信号 %s,准备退出", signum)
        self._stop.set()


def main() -> None:
    Service().run()


if __name__ == "__main__":
    main()
