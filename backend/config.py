"""P1 配置：优先读环境变量，没有再读项目根 .env，最后用内置缺省值。

两条铁律：
1. API Key 绝不写进代码（安全红线），也不在 import 时校验——
   不带 --with-ai 的老用法（P0 契约）必须照常能跑，校验推迟到真正发请求那一刻；
2. 每个值都留环境变量入口，CI 或临时换模型不用改任何文件。
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent   # repo-atlas/
_ENV_FILE = PROJECT_ROOT / ".env"


def _load_dotenv(path: Path) -> dict[str, str]:
    """手写 .env 解析（十来行），不值得为此引入 python-dotenv。"""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        # 跳过空行和 # 注释；partition 天然容忍 KEY = value 两侧空格
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


_DOTENV = _load_dotenv(_ENV_FILE)


def _from_env(name: str, default: str = "") -> str:
    return os.environ.get(name) or _DOTENV.get(name) or default


def _from_env_int(name: str, default: int) -> int:
    # 复用 _from_env：环境变量和 .env 都能覆盖数字配置，
    # 否则 .env 里写 LLM_TIMEOUT=90 不生效，容易把人绕晕
    raw = _from_env(name)
    return int(raw) if raw.isdigit() else default


def _from_env_float(name: str, default: float) -> float:
    try:
        return float(_from_env(name) or default)
    except ValueError:
        return default


# Key 的名字兼容两套：环境变量走 GLM_API_KEY / ZHIPU_API_KEY，.env 里是 ZHIPU_API_KEY
GLM_API_KEY: str = (
    os.environ.get("GLM_API_KEY")
    or os.environ.get("ZHIPU_API_KEY")
    or _DOTENV.get("GLM_API_KEY")
    or _DOTENV.get("ZHIPU_API_KEY", "")
)
GLM_MODEL: str = _from_env("GLM_MODEL", "glm-4.7-flash")
GLM_BASE_URL: str = _from_env(
    "GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions"
)
LLM_TIMEOUT: int = _from_env_int("LLM_TIMEOUT", 60)
# 免费档并发上限就是 1：多开线程只会互相挤掉、整批 429，所以默认串行
LLM_MAX_WORKERS: int = _from_env_int("LLM_MAX_WORKERS", 1)
# 两个真实请求之间的最小间隔（秒），配合并发 1 避免触发限流
LLM_MIN_INTERVAL: float = _from_env_float("LLM_MIN_INTERVAL", 0.5)
MAX_FILE_CHARS: int = _from_env_int("MAX_FILE_CHARS", 6000)
PROMPT_VERSION: str = _from_env("PROMPT_VERSION", "v1")
DB_PATH: Path = Path(
    _from_env("DB_PATH", str(Path(__file__).resolve().parent / "data" / "atlas.db"))
)


def require_api_key() -> str:
    """真正要发请求时才调用：缺 Key 给一句人话，而不是 import 阶段就崩。"""
    if GLM_API_KEY:
        return GLM_API_KEY
    raise RuntimeError(
        "缺少 API Key：请在项目根目录的 .env 里写一行 ZHIPU_API_KEY=<你的Key>，"
        "或设置环境变量 GLM_API_KEY"
    )
