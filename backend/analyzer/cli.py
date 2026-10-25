"""命令行入口：python -m analyzer.cli <本地路径或git地址> [--json]。

analyze() 是给 P1/P2 复用的总入口；main() 只包一层参数解析和输出格式选择。
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from analyzer.ast_parser import build_graph, extract_imports, resolve_raw_imports
from analyzer.repo_loader import (
    build_module_index,
    clone_or_locate,
    iter_python_files,
    module_of,
)

logger = logging.getLogger(__name__)


def analyze(repo: str) -> dict:
    """总入口：定位仓库 → 收集文件 → 建模块表 → 逐文件解析归类 → 拼 edges。"""
    root = clone_or_locate(repo)
    files = iter_python_files(root)
    module_index = build_module_index(root, files)
    file_entries: list[dict] = []
    skipped: list[dict] = []
    for path in files:
        rel = path.relative_to(root)
        raw, error = extract_imports(path)
        if error is not None:
            # 一个坏文件不能拖垮整批：记下原因继续，最后汇总进 skipped
            logger.warning("跳过 %s: %s", rel.as_posix(), error)
            skipped.append({"path": rel.as_posix(), "reason": error})
            continue
        internal, external, stdlib = resolve_raw_imports(
            raw, module_of(rel), module_index,
        )
        file_entries.append({
            "path": rel.as_posix(),
            "module": module_of(rel),
            "internal": internal,
            "external": external,
            "stdlib": stdlib,
        })
    external_all = sorted({name for entry in file_entries for name in entry["external"]})
    analysis = {
        "root": root.as_posix(),   # 对外路径一律正斜杠，前端和 SQLite 里才不会混
        "files": file_entries,
        "edges": [],
        "external_all": external_all,
        "skipped": skipped,
    }
    return build_graph(analysis)


def render_text(analysis: dict) -> str:
    """渲染成人看的文本：概览 → 文件树 → 项目内依赖。"""
    lines = [
        f"仓库: {Path(analysis['root']).name}",
        _summary_line(analysis),
        f"跳过: {len(analysis['skipped'])} 个",
        "",
        "文件清单",
    ]
    lines.extend(_render_tree(analysis["files"]))
    lines.append("")
    lines.append("项目内依赖")
    lines.extend(_render_edges(analysis["edges"]))
    return "\n".join(lines)


def _summary_line(analysis: dict) -> str:
    external = ", ".join(analysis["external_all"]) or "(无)"
    return (
        f"Python 文件: {len(analysis['files'])} 个 | "
        f"内部依赖: {len(analysis['edges'])} 条 | 第三方: {external}"
    )


def _render_tree(file_entries: list[dict]) -> list[str]:
    """按目录层级画树，右侧标注每个文件的 internal/external 依赖。"""
    # 嵌套 dict 当树用：值是 dict 表示目录，None 表示文件
    root: dict = {}
    for entry in file_entries:
        node = root
        parts = entry["path"].split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = None
    info = {entry["path"]: entry for entry in file_entries}
    rows: list[tuple[str, str]] = []
    _walk_tree(root, "", [], rows, info)
    # ← 统一对齐到最长的一行，眼睛扫一列就能比
    width = max((len(display) for display, _ in rows), default=0)
    lines = []
    for display, annotation in rows:
        if annotation:
            lines.append(f"{display:<{width}}  {annotation}")
        else:
            lines.append(display)
    return lines


def _walk_tree(
    node: dict,
    prefix: str,
    parts: list[str],
    rows: list[tuple[str, str]],
    info: dict[str, dict],
) -> None:
    """递归画一层。目录和文件混在一起按名字排序，找东西最快。"""
    children = sorted(node)
    for i, name in enumerate(children):
        last = i == len(children) - 1
        connector = "└── " if last else "├── "
        child = node[name]
        if isinstance(child, dict):
            rows.append((f"{prefix}{connector}{name}/", ""))
            # 兄弟里排最后的，下一层前缀用空格收尾，其余继续画竖线
            _walk_tree(child, prefix + ("    " if last else "│   "), parts + [name], rows, info)
        else:
            path = "/".join(parts + [name])
            rows.append((f"{prefix}{connector}{name}", _annotation(info[path])))


def _annotation(entry: dict) -> str:
    """文件行右侧的标注：internal 永远显示，external 有才显示。"""
    internal = ", ".join(entry["internal"]) or "(无)"
    text = f"← internal: {internal}"
    if entry["external"]:
        text += f"   external: {', '.join(entry['external'])}"
    return text


def _render_edges(edges: list[dict]) -> list[str]:
    if not edges:
        return ["  (无)"]
    width = max(len(edge["source"]) for edge in edges) + 2
    return [
        f"  {edge['source']:<{width}}→  {edge['target']}" for edge in edges
    ]


def main(argv: list[str] | None = None) -> int:
    """命令行入口：--json 输出契约 JSON，否则输出渲染文本；成功 0，出错非 0。"""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        prog="python -m analyzer.cli",
        description="解析一个 Python 仓库的文件结构与 import 依赖（P0）",
    )
    parser.add_argument("repo", help="本地仓库路径，或 http(s)/git@ 开头的 git 地址")
    parser.add_argument("--json", action="store_true", help="输出契约 JSON 而不是渲染文本")
    args = parser.parse_args(argv)
    try:
        analysis = analyze(args.repo)
    except ValueError as exc:
        # clone_or_locate 抛的都是"地址给错了"这类可预期错误，打印一句而不是甩 traceback
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    else:
        print(render_text(analysis))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
