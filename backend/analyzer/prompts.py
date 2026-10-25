"""提示词：让模型只产出 role / summary / key_entities 三个字段的 JSON。

分工原则：结构信息（依赖关系）由 P0 的静态分析确定后作为上下文喂进去，
模型只负责语义理解——AI 不推测结构，算法不编语义。
"""

import ast

import config

PROMPT_VERSION = config.PROMPT_VERSION

_SYSTEM_PROMPT = """你是一个 Python 代码阅读助手。你会收到一个源文件，以及由静态分析确定的该文件内部依赖列表。
只输出一个 JSON 对象：第一个字符必须是 {，最后一个字符必须是 }。
不要 markdown 代码块，不要任何解释文字。字段固定为：
- role: 一句话，不超过 30 个字，说明这个模块在项目里扮演的角色
- summary: 2~3 句话，说明这个文件做什么、关键类/函数是哪些
- key_entities: 这个文件里最重要的类名/函数名组成的 JSON 数组，最多 5 个
全部用中文。"""


def truncate_source(code: str, limit: int) -> str:
    """超长文件只保留 模块 docstring、顶层 import 区、所有 def/class 的签名行，丢弃函数体。

    文件头和签名是信息密度最高的部分；函数体对"一句话说明"几乎没有增益。
    """
    if len(code) <= limit:
        return code
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # 能走到这里的文件 P0 都解析成功过；这行只是兜底，保证任何输入都不炸
        return code[:limit] + "\n# ... 已截断 ..."
    keep: set[int] = set()   # 要保留的行号（按 0 数）
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            keep.update(range(node.lineno - 1, node.end_lineno))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            keep.update(range(node.lineno - 1, node.end_lineno))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([d.lineno for d in node.decorator_list] + [node.lineno])
            # 签名的最后一行 = body 第一行的上一行，多行参数列表也覆盖得住
            end = node.body[0].lineno - 1
            keep.update(range(start - 1, end))
    lines = code.splitlines()
    picked = "\n".join(lines[i] for i in sorted(keep) if i < len(lines))
    if len(picked) > limit:
        picked = picked[:limit]
    # 明确告诉模型文件不完整，避免它把"没看到"当"不存在"
    return picked + "\n# ... 已截断 ..."


def build_messages(path: str, code: str, internal: list[str]) -> tuple[str, str]:
    """返回 (system, user)。代码先进 truncate_source，防止长文件把 token 撑爆。"""
    deps = ", ".join(internal) if internal else "(无)"
    user = (
        f"文件路径: {path}\n"
        f"静态分析已确定的内部依赖（不要推测或补充依赖关系）: {deps}\n\n"
        f"源代码:\n{truncate_source(code, config.MAX_FILE_CHARS)}"
    )
    return _SYSTEM_PROMPT, user
