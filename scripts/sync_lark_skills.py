#!/usr/bin/env python3
"""把 lark-cli 内置的官方 Agent Skills 全量导出到 pi 的技能发现目录。

用法:
  python3 scripts/sync_lark_skills.py [输出目录]

默认输出 ~/.pi/agent/skills(pi 全局技能目录,容器内对应挂载的 /root/.pi/agent/skills)。
lark-cli 的 skills 只提供 list/read(内容编译进二进制、随版本更新),
本脚本读取每个 SKILL.md 及其引用的全部 .md(含跨技能引用),落盘为标准 pi 技能布局:
  <输出目录>/<skill-name>/SKILL.md
  <输出目录>/<skill-name>/references/*.md
"""

from __future__ import annotations

import json
import posixpath
import re
import subprocess
import sys
from pathlib import Path

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+?\.md)(#[^)]*)?\)")


def fetch(path: str) -> str | None:
    """读取 lark-cli 内嵌技能文件;不存在或失败返回 None。"""
    proc = subprocess.run(
        ["lark-cli", "skills", "read", path], capture_output=True, text=True, timeout=60
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out or out.lstrip().startswith("{"):
        return None  # 错误输出是 JSON envelope
    return out


def export_skill(name: str, out_root: Path) -> tuple[int, list[str]]:
    """导出一个技能及其引用文件,返回 (文件数, 警告)。"""
    files: dict[str, str] = {}  # 技能内相对路径 -> 内容
    warnings: list[str] = []

    def crawl(virtual_path: str, content: str, seen: set[str]) -> None:
        """virtual_path 形如 <skill>/<sub> 或 ../<other-skill>/<sub>。"""
        base = posixpath.dirname(virtual_path)  # 链接相对的目录
        for link in LINK_RE.findall(content):
            target = link[0]
            resolved = posixpath.normpath(posixpath.join(base, target))
            if resolved in seen:
                continue
            seen.add(resolved)
            body = fetch(resolved)
            if body is None:
                warnings.append(f"{name}: 引用抓取失败 {resolved}")
                continue
            files[resolvable_location(resolved)] = body
            crawl(resolved, body, seen)

    content = fetch(name)
    if content is None:
        return 0, [f"{name}: SKILL.md 读取失败"]
    files[f"{name}/SKILL.md"] = content
    # 关键:从 <skill>/SKILL.md 开始爬,保证链接相对的 base 是技能根目录;
    # ../ 开头的跨技能链接经 normpath 归一后即全局路径(<other-skill>/...)
    crawl(f"{name}/SKILL.md", content, {f"{name}/SKILL.md"})

    for rel, body in files.items():
        dest = out_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
    return len(files), warnings


def resolvable_location(virtual_path: str) -> str:
    """跨技能引用(.. 前缀已归一化掉)直接落全局路径,同技能路径原样。"""
    return virtual_path


def main() -> None:
    out_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".pi/agent/skills"
    listed = json.loads(subprocess.run(
        ["lark-cli", "skills", "list"], capture_output=True, text=True, check=True
    ).stdout)["skills"]

    total_files, all_warnings = 0, []
    for skill in listed:
        n, warns = export_skill(skill["name"], out_root)
        total_files += n
        all_warnings += warns
        print(f"  {skill['name']}: {n} 个文件")

    print(f"\n完成:{len(listed)} 个技能,共 {total_files} 个文件 -> {out_root}")
    for w in all_warnings:
        print("警告:", w)
    if all_warnings:
        sys.exit(1)


if __name__ == "__main__":
    main()
