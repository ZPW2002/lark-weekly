"""事件消费子进程管理。

每个 EventKey 对应一个 `lark-cli event consume` 子进程(官方设计:one consume, one EventKey),
多个消费者共享 CLI 内部的事件总线守护进程。stderr 等待就绪标记后才认为订阅成功;
子进程意外退出时自动重启(指数退避,上限 60s)。
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time

from ..settings import settings

log = logging.getLogger(__name__)

READY_MARKER = "[event] ready"


class EventConsumer:
    def __init__(self, event_key: str, handler, *, identity: str = "bot"):
        self.event_key = event_key
        self.handler = handler
        self.identity = identity
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._precondition_hint = ""  # 需要人工到开发者后台处理时置位

    # -- 生命周期 ----------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name=f"consume-{self.event_key}", daemon=True)
        self._thread.start()

    def stop(self, timeout: int = 15) -> None:
        self._stop.set()
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()  # stdin EOF = 优雅退出(官方契约)
            except Exception:
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.terminate()
        if self._thread:
            self._thread.join(timeout=timeout)

    # -- 内部 ---------------------------------------------------------------

    def _loop(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            started = time.time()
            self._precondition_hint = ""
            try:
                self._run_once()
            except Exception:
                log.exception("事件消费 %s 异常退出", self.event_key)
            if self._stop.is_set():
                break
            if self._precondition_hint:
                # 需要人工在开发者后台开通事件/回调,退避 5 分钟,避免刷日志
                wait = 300
                log.warning("事件消费 %s 需要人工介入:%s", self.event_key, self._precondition_hint)
            else:
                alive = time.time() - started
                wait = backoff if alive < 60 else 2  # 稳定运行过则快速重启
                log.warning("事件消费 %s 将在 %ss 后重启", self.event_key, wait)
                backoff = min(backoff * 2, 60)
            self._stop.wait(wait)

    def _run_once(self) -> None:
        args = [settings.lark_bin, "event", "consume", self.event_key, "--as", self.identity]
        log.info("启动事件消费: %s", " ".join(args))
        proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self._proc = proc

        stderr_thread = threading.Thread(target=self._drain_stderr, args=(proc,), daemon=True)
        stderr_thread.start()

        assert proc.stdin is not None
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    import json
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("%s 输出非 JSON 行: %s", self.event_key, line[:200])
                    continue
                try:
                    self.handler(evt)
                except Exception:
                    log.exception("处理 %s 事件失败: %s", self.event_key, str(evt)[:200])
        finally:
            if proc.poll() is None and not self._stop.is_set():
                proc.terminate()

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        ready_seen = False
        for line in proc.stderr:
            line = line.strip()
            if not line:
                continue
            if not ready_seen and READY_MARKER in line:
                ready_seen = True
                log.info("事件订阅就绪: %s", line)
                continue
            if "failed_precondition" in line or "not subscribed" in line:
                self._precondition_hint = _extract_hint(line) or line[:300]
            log.debug("[%s stderr] %s", self.event_key, line)
        if not ready_seen and not self._stop.is_set():
            log.warning("事件消费 %s 未见就绪标记即退出", self.event_key)


def _extract_hint(text: str) -> str:
    import json

    idx = text.find("{")
    if idx < 0:
        return ""
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[idx:])
        err = obj.get("error") or {}
        return str(err.get("hint") or err.get("message") or "")
    except json.JSONDecodeError:
        return ""
