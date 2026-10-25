"""把单个 .py 文件里的 import 语句抽出来，并归类成 内部 / 第三方 / 标准库。"""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RawImport:
    module: str        # import a.b → "a.b"；from a.b import c → "a.b"
    names: list[str]   # from a.b import c,d → ["c","d"]；import a.b → []
    level: int         # from .x import y → 1；from ..x import y → 2；绝对导入 → 0


def extract_imports(path: Path) -> tuple[list[RawImport], str | None]:
    """解析单个文件，返回 (import 列表, 错误信息)。读不了或语法错时返回 ([], 原因)，不抛异常。"""
    try:
        # utf-8-sig 兼容带 BOM 的文件（Windows 记事本存的代码常有），无 BOM 时等价 utf-8
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError, ValueError) as exc:
        # ValueError 也一起兜：带 \x00 的文件 ast.parse 抛的是它，不兜会炸掉整批分析。
        # 原因带上异常类名，用户不用猜是语法错还是编码错
        return [], f"{type(exc).__name__}: {exc}"
    imports: list[RawImport] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # import a.b, c.d 是一条语句两个目标，拆开存，归类时不用再切
                imports.append(RawImport(module=alias.name, names=[], level=0))
        elif isinstance(node, ast.ImportFrom):
            # from . import x 时 node.module 是 None，统一成空串，后面按基准包拼
            imports.append(RawImport(
                module=node.module or "",
                names=[alias.name for alias in node.names],
                level=node.level,
            ))
    return imports, None


def resolve_raw_imports(
    raw: list[RawImport],
    current_module: str,
    module_index: dict[str, str],
) -> tuple[list[str], list[str], list[str]]:
    """把 RawImport 归类成 (internal 模块名列表, external 列表, stdlib 列表)。

    判定顺序：先查项目内 → 再查标准库 → 剩下都算第三方。
    """
    internal: set[str] = set()
    external: set[str] = set()
    stdlib: set[str] = set()
    for imp in raw:
        if imp.level > 0:
            _resolve_relative(imp, current_module, module_index, internal)
        else:
            _resolve_absolute(imp, module_index, internal, external, stdlib)
    # 指向自己的 import（只会出现在包的 __init__.py 里）不算依赖
    internal.discard(current_module)
    return sorted(internal), sorted(external), sorted(stdlib)


def _resolve_relative(
    imp: RawImport,
    current_module: str,
    module_index: dict[str, str],
    internal: set[str],
) -> None:
    """相对导入只可能指向项目内：拿 current_module 往上退 level 层得到基准包再拼。"""
    # __init__.py 自己就是包，from . import x 的 "." 指它本身，要比普通模块少退一层
    is_package = module_index.get(current_module, "").endswith("__init__.py")
    parts = current_module.split(".") if current_module else []
    up = imp.level - 1 if is_package else imp.level
    if up > len(parts):
        return  # 退出了仓库顶层（脚本被误当模块运行时的写法），这条放弃
    base = ".".join(parts[: len(parts) - up])
    # from . import x（module 为空）时基准包本身会混进 internal：
    # 基准若正是当前文件所在的包，它早在本文件加载前就已加载完，不是真依赖，
    # 记进去只会造出「父包 ⇄ 子模块」的假环；from .. import x 的基准是祖先包，照旧记
    own_package = current_module if is_package else ".".join(parts[:-1])
    drop_base = imp.module == "" and base == own_package
    target = f"{base}.{imp.module}" if imp.module and base else (imp.module or base)
    if target in module_index and not drop_base:
        internal.add(target)
    for name in imp.names:
        # from . import x 的 x 可能是子模块，也可能是包 __init__ 里定义的名字，查表见真章
        child = f"{target}.{name}" if target else name
        if child in module_index:
            internal.add(child)


def _resolve_absolute(
    imp: RawImport,
    module_index: dict[str, str],
    internal: set[str],
    external: set[str],
    stdlib: set[str],
) -> None:
    if not imp.names:
        # import a.b.c：c 一定是模块。整条不在项目里就取存在的最长前缀
        #（import a.b 也会执行 a/__init__.py，所以记 a.b 是真依赖）
        matched = _longest_internal_prefix(imp.module, module_index)
        if matched:
            internal.add(matched)
        else:
            _classify_by_top(imp.module, stdlib, external)
        return
    # from a.b import c：主干 a.b 先查；c 可能是子模块（a/b/c.py 或 a/b/c/__init__.py），逐个查
    hit = False
    if imp.module in module_index:
        internal.add(imp.module)
        hit = True
    for name in imp.names:
        child = f"{imp.module}.{name}"
        if child in module_index:
            internal.add(child)
            hit = True
    if not hit:
        _classify_by_top(imp.module, stdlib, external)


def _longest_internal_prefix(module: str, module_index: dict[str, str]) -> str | None:
    """从整条名往短里试，返回第一个真实存在于项目里的前缀。"""
    parts = module.split(".")
    for size in range(len(parts), 0, -1):
        candidate = ".".join(parts[:size])
        if candidate in module_index:
            return candidate
    return None


def _classify_by_top(module: str, stdlib: set[str], external: set[str]) -> None:
    """非内部的导入只记顶层包名：playwright.sync_api 记成 playwright，os.path 记成 os。"""
    top = module.split(".")[0]
    if top in sys.stdlib_module_names:
        stdlib.add(top)
    else:
        external.add(top)


def build_graph(analysis: dict) -> dict:
    """把每个文件的 internal 模块名翻译成 path，得到去重、无自环、排好序的 edges。

    模块名→路径的映射直接从 files 里重建（每条都带自己的 module），不依赖外部传参。
    """
    module_to_path = {entry["module"]: entry["path"] for entry in analysis["files"]}
    seen: set[tuple[str, str]] = set()
    for entry in analysis["files"]:
        for module in entry["internal"]:
            target = module_to_path.get(module)
            if target is None:
                continue  # 指向被跳过的坏文件就没有边可画，前端画图时它会自然缺席
            if target == entry["path"]:
                continue  # 自环去掉；A→B→A 的环保留
            seen.add((entry["path"], target))
    analysis["edges"] = [
        {"source": source, "target": target} for source, target in sorted(seen)
    ]
    return analysis
