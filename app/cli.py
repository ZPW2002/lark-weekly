"""手动触发/调试入口。

用法示例:
  python -m app.cli config                 # 查看生效配置
  python -m app.cli notify --dry-run       # 预览提醒文案
  python -m app.cli remind --dry-run       # 预览催交名单
  python -m app.cli summarize --dry-run    # 生成汇总(不发送)
  python -m app.cli mail --week 2026-W36 --dry-run
  python -m app.cli ask "我今天有什么日程"  # 测试 pi 智能回复
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config_store import ConfigStore, ConfigError
from .events.handlers import EventHandlers
from .jobs import mailer, notify, remind, summarize
from .lark import LarkError
from .pi_agent import PiError
from .settings import settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="app.cli", description="周报自动化手动调试工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("config", help="查看生效配置")

    for name in ("notify", "remind", "summarize"):
        p = sub.add_parser(name, help=f"手动执行 {name}")
        p.add_argument("--dry-run", action="store_true", help="只预览不发送")
        if name == "summarize":
            p.add_argument("--week", default=None, help="指定周次,如 2026-W36")

    p = sub.add_parser("mail", help="发送周报邮件(需先 summarize)")
    p.add_argument("--week", required=True, help="周次,如 2026-W36")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("ask", help="测试 pi 智能回复(不经过飞书)")
    p.add_argument("text", help="模拟用户消息")

    sub.add_parser("events", help="只启动事件消费(前台,Ctrl+C 退出),用于调试")

    args = parser.parse_args()
    store = ConfigStore()

    try:
        if args.cmd == "config":
            import json
            print(json.dumps(store.get(force=True), ensure_ascii=False, indent=2))
        elif args.cmd == "notify":
            notify.run(store, dry_run=args.dry_run)
        elif args.cmd == "remind":
            remind.run(store, dry_run=args.dry_run)
        elif args.cmd == "summarize":
            digest = summarize.run(store, dry_run=args.dry_run, week=args.week)
            if args.dry_run:
                print("\n===== 汇总预览 =====\n" + digest)
        elif args.cmd == "mail":
            mailer.run(store, args.week, dry_run=args.dry_run)
        elif args.cmd == "ask":
            handlers = EventHandlers(store)
            cfg = store.get(force=True)
            reply = _ask(handlers, args.text, cfg)
            print(reply)
        elif args.cmd == "events":
            _run_events(store)
    except (ConfigError, PiError, LarkError) as e:
        hint = getattr(e, "hint", "")
        print(f"错误: {e}" + (f"\n提示: {hint}" if hint else ""), file=sys.stderr)
        sys.exit(1)


def _ask(handlers: EventHandlers, text: str, cfg: dict) -> str:
    from . import pi_agent
    return pi_agent.agent_reply(text, {"会话类型": "调试", "发送人": "cli"}, cfg)


def _run_events(store: ConfigStore) -> None:
    import signal
    import threading

    from .events.consumer import EventConsumer

    handlers = EventHandlers(store)
    consumers = [
        EventConsumer("im.message.receive_v1", handlers.handle_im_message),
        EventConsumer("card.action.trigger", handlers.handle_card_action),
    ]
    for c in consumers:
        c.start()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print("事件消费运行中,Ctrl+C 退出…")
    stop.wait()
    for c in consumers:
        c.stop()


if __name__ == "__main__":
    main()
