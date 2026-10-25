"""GLM 对话接口的最薄封装：发消息、拿回文本、带 token 数。

重试按响应体里的业务码 error.code 分流，不看 HTTP 状态盲试：
1302/1305 是限流类，退避后值得再试；1308/1113/1301 这类重试也没用，当场报错。
"""

import logging
import threading
import time
from dataclasses import dataclass

import requests

import config

logger = logging.getLogger(__name__)


class LLMError(Exception):
    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        # fatal=True 表示"这个错换一个文件重试也一样"（Key 无效这类），
        # 上层应立即终止整批，而不是把所有文件挨个失败一遍
        self.fatal = fatal


@dataclass
class LLMResult:
    content: str
    tokens_in: int
    tokens_out: int


# 免费档并发上限 1：用一把全局锁串行所有请求，并保证任意两次真实请求
# 之间至少隔 LLM_MIN_INTERVAL 秒——哪怕以后把并发调回去，也不会背靠背打点
_request_lock = threading.Lock()
_last_request_time = 0.0

_RETRYABLE_CODES = {"1302", "1305"}   # 限流/并发超限：退避后重试有意义


def chat(system: str, user: str) -> LLMResult:
    """发一次对话请求。可重试错误按 2s/4s/8s 退避最多 3 轮；不可重试错误当场抛。"""
    url = _chat_url(config.GLM_BASE_URL)
    payload = {
        "model": config.GLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        # 思考模式必须显式关掉：不关的话 reasoning 文本会冲掉"只回 JSON"的约定，耗时还翻倍
        "thinking": {"type": "disabled"},
    }
    headers = {"Authorization": f"Bearer {config.require_api_key()}"}
    delays = (2, 4, 8)
    last_error = "未知错误"
    for attempt in range(len(delays) + 1):
        _throttle()
        try:
            resp = requests.post(
                url, json=payload, headers=headers, timeout=config.LLM_TIMEOUT,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            # 只记异常类型名：请求头（含 Key）绝不会出现在任何错误文本里
            last_error = f"网络异常: {type(exc).__name__}"
            retryable = True
        else:
            if resp.ok:
                return _parse_response(resp)
            message, retryable, fatal = _classify_error(resp)
            if fatal:
                raise LLMError(message, fatal=True)
            if not retryable:
                raise LLMError(message)
            last_error = message
        if attempt < len(delays):
            logger.warning("请求失败（%s），%s 秒后重试", last_error, delays[attempt])
            time.sleep(delays[attempt])
    raise LLMError(f"重试后仍失败: {last_error}")


def _throttle() -> None:
    """发请求前调用：锁住 + 对齐上次请求时间，强制拉开请求间隔。"""
    global _last_request_time
    with _request_lock:
        idle = time.monotonic() - _last_request_time
        if idle < config.LLM_MIN_INTERVAL:
            time.sleep(config.LLM_MIN_INTERVAL - idle)
        _last_request_time = time.monotonic()


def _classify_error(resp: requests.Response) -> tuple[str, bool, bool]:
    """把非 2xx 响应分到 (错误信息, 可否重试, 是否致命)。"""
    # Key 问题先于业务码判断：BigModel 的 401 响应体里也带 code，别被它带偏成分流分支
    if resp.status_code in (401, 403):
        return f"API Key 无效（HTTP {resp.status_code}）", False, True
    body = _safe_json(resp)
    error = body.get("error") if body else None
    info = error if isinstance(error, dict) else {}
    code = str(info.get("code", ""))
    if not code:
        # 拿不到业务码就没有重试依据
        return f"HTTP {resp.status_code}（无业务码）", False, False
    detail = str(info.get("message", ""))[:80]
    if code in _RETRYABLE_CODES:
        return f"业务码 {code}: {detail}", True, False
    if code == "1308":
        # 配额窗口未到点：把服务端给的解冻时间带出来，重试是浪费
        flush_at = info.get("next_flush_time") or body.get("next_flush_time", "未知")
        return f"业务码 1308（配额窗口未刷新）: 下次可用时间 {flush_at}", False, False
    # 1113（账户欠费）、1301（内容风控）及其余业务码：当场失败
    return f"业务码 {code}: {detail}", False, False


def _safe_json(resp: requests.Response) -> dict:
    """尽力解析响应体；解析不了就当没有业务码处理。"""
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _chat_url(base_url: str) -> str:
    """.env 里习惯把 GLM_BASE_URL 写到 /v4 为止的"基础地址"，请求时要补上对话路径；
    配置里写了完整端点也能兼容。"""
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _parse_response(resp: requests.Response) -> LLMResult:
    """把 200 响应拆成 正文 + token 数；结构不合预期按一次失败处理。"""
    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens_in = data["usage"]["prompt_tokens"]
        tokens_out = data["usage"]["completion_tokens"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"响应结构异常: {type(exc).__name__}") from exc
    return LLMResult(content=content, tokens_in=tokens_in, tokens_out=tokens_out)
