"""负责把"一个仓库地址"变成"一堆可分析的 .py 文件"。

这一层只管文件定位和模块名换算，不做任何语法分析（那是 ast_parser 的事）。
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# 克隆远端仓库的落盘位置：backend/data/clones。
# 用 __file__ 往上推而不是写死绝对路径，整个项目挪窝也不用改这行。
CLONE_DIR = Path(__file__).resolve().parent.parent / "data" / "clones"

# 这些目录里的 .py 是工具生成或第三方自带的，读进来只会把依赖图搅乱。
SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", "node_modules",
    "build", "dist", ".tox", ".mypy_cache", ".pytest_cache",
    "site-packages", "migrations",
}


def clone_or_locate(repo: str, dest_root: Path = CLONE_DIR) -> Path:
    """repo 是本地路径就直接返回；是 git 地址就浅克隆到 dest_root 下并复用。"""
    if repo.startswith(("http://", "https://", "git@")):
        return _clone_remote(repo, dest_root)
    local = Path(repo)
    if not local.exists():
        raise ValueError(f"路径不存在: {repo}")
    if not local.is_dir():
        # 分析的对象是"整个仓库"，传单个文件没有依赖图可画
        raise ValueError(f"不是目录，只能分析整个仓库: {repo}")
    # resolve() 把 .. 和符号链接理顺，后面 relative_to 才不会算错
    return local.resolve()


def _clone_remote(repo: str, dest_root: Path) -> Path:
    """浅克隆远端仓库；本地已有同名目录就直接复用，避免重复拉取。"""
    name = repo.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    if not name:
        raise ValueError(f"无法从地址里解析出仓库名: {repo}")
    dest = dest_root / name
    if dest.exists():
        logger.info("复用已克隆的仓库: %s", dest)
        return dest.resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo, str(dest)],
            capture_output=True, text=True,
        )
    except FileNotFoundError as exc:
        # git 没装是环境问题，得让用户看懂而不是给一个 FileNotFoundError 堆栈
        raise ValueError("这台机器上没有 git 命令，无法克隆远端仓库") from exc
    if result.returncode != 0:
        raise ValueError(f"git clone 失败: {result.stderr.strip()}")
    return dest.resolve()


def iter_python_files(root: Path) -> list[Path]:
    """递归收集所有 .py，跳过 SKIP_DIRS 里的目录，返回排好序的绝对路径。"""
    files: list[Path] = []
    for path in root.rglob("*.py"):
        # 用 rglob 而不是 os.walk：rglob 返回 Path 对象，后面拼相对路径不用再做字符串拼接
        parents = path.relative_to(root).parts[:-1]
        if any(part in SKIP_DIRS for part in parents):
            continue
        files.append(path)
    return sorted(files)


def module_of(rel_path: Path) -> str:
    """相对路径 → 模块名。__init__.py 代表它所在的包，不是一个叫 __init__ 的模块。"""
    parts = rel_path.with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def build_module_index(root: Path, files: list[Path]) -> dict[str, str]:
    """返回 {模块名: 相对路径}。

    a/b.py 和 a/b/__init__.py 归到同一个键 a.b，所以后续查表天然覆盖两种归属；
    同名冲突时让包（__init__.py）赢，和 Python"包优先于同名模块"的规则一致。
    """
    plain: dict[str, str] = {}
    packages: dict[str, str] = {}
    for f in files:
        rel = f.relative_to(root)
        name = module_of(rel)
        if not name:
            continue  # 仓库根上的 __init__.py 拿不到模块名，进不了索引
        bucket = packages if rel.name == "__init__.py" else plain
        bucket[name] = rel.as_posix()
    index = dict(plain)
    index.update(packages)
    return index
