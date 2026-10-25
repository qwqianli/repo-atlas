"""给 analysis["files"] 里每个文件补 AI 说明：先查缓存，未命中的进线程池调 GLM，结果落库。"""

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import config
from analyzer import llm_client
from analyzer.llm_client import LLMError, LLMResult
from analyzer.prompts import build_messages
from analyzer.store import SummaryStore

logger = logging.getLogger(__name__)


def summarize_files(analysis: dict, root: Path, on_progress: Callable[[int, int, str], None] | None = None) -> dict:
    """原地给每个文件补 role/summary/key_entities/cached，并写入 ai_stats / ai_errors。

    on_progress(done, total, path) 是给 P3 的 SSE 进度推送预留的回调，
    P1 没有调用方也必须留着——现在不留，P3 就得回头改这里的签名。
    """
    try:
        config.require_api_key()
    except RuntimeError as exc:
        # 缺 Key 属于致命错：转成 LLMError(fatal) 让 cli 以退出码 2 结束
        raise LLMError(str(exc), fatal=True) from exc

    start = time.perf_counter()
    stats = {"hit": 0, "miss": 0, "failed": 0, "tokens_in": 0, "tokens_out": 0, "elapsed_ms": 0}
    errors: list[dict] = []
    store = SummaryStore(config.DB_PATH)
    try:
        _run_all(analysis["files"], root, store, stats, errors, on_progress)
    finally:
        store.close()
    stats["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
    analysis["ai_stats"] = stats
    analysis["ai_errors"] = errors
    return analysis


def _run_all(files: list[dict], root: Path, store: SummaryStore, stats: dict,
             errors: list[dict], on_progress: Callable[[int, int, str], None] | None) -> None:
    """第一阶段（主线程）：逐个查缓存。命中直接用，未命中备好提示词等进池子。"""
    total = len(files)
    done = 0
    tasks: list[tuple[dict, str, str, str]] = []   # (entry, cache_key, system, user)
    for entry in files:
        # 读文件、算 hash、查缓存都留在主线程：进线程池的只有网络调用这一件事
        source = _read_source(root / entry["path"])
        key = _cache_key(source)
        cached = store.get(key)
        if cached is not None:
            _apply(entry, cached["role"], cached["summary"], cached["key_entities"], cached=True)
            stats["hit"] += 1
            done += 1
            if on_progress:
                on_progress(done, total, entry["path"])
        else:
            stats["miss"] += 1
            system, user = build_messages(entry["path"], source, entry["internal"])
            tasks.append((entry, key, system, user))
    _run_requests(tasks, store, stats, errors, on_progress)


def _run_requests(tasks: list[tuple[dict, str, str, str]], store: SummaryStore,
                  stats: dict, errors: list[dict],
                  on_progress: Callable[[int, int, str], None] | None) -> None:
    """第二阶段：线程池只包 llm_client.chat；解析、落库、统计都在主线程做。"""
    done = stats["hit"]   # 缓存命中的部分第一阶段就完成了
    total = stats["hit"] + len(tasks)
    with ThreadPoolExecutor(max_workers=config.LLM_MAX_WORKERS) as pool:
        future_map = {
            pool.submit(llm_client.chat, system, user): (entry, key)
            for entry, key, system, user in tasks
        }
        for future in as_completed(future_map):
            entry, key = future_map[future]
            try:
                result = future.result()
                role, summary, entities = _parse_result(result.content)
            except LLMError as exc:
                if exc.fatal:
                    # Key 无效这类错，剩下的文件重试也一样，立即整体终止
                    raise
                _record_failure(entry, str(exc), stats, errors)
            except ValueError as exc:
                # 模型没按约定回 JSON：算单文件失败，不牵连其他文件
                _record_failure(entry, f"AI 返回解析失败: {exc}", stats, errors)
            else:
                _save_success(entry, key, role, summary, entities, result, store, stats)
            done += 1
            if on_progress:
                on_progress(done, total, entry["path"])


def _record_failure(entry: dict, reason: str, stats: dict, errors: list[dict]) -> None:
    """单文件失败：记进 ai_errors、三个字段置 None，整批继续跑。"""
    stats["failed"] += 1
    errors.append({"path": entry["path"], "reason": reason})
    _apply(entry, None, None, None, cached=False)
    logger.warning("AI 摘要失败 %s: %s", entry["path"], reason)


def _save_success(entry: dict, key: str, role: str, summary: str, entities: list[str],
                  result: LLMResult, store: SummaryStore, stats: dict) -> None:
    _apply(entry, role, summary, entities, cached=False)
    store.put(key, {
        "model": config.GLM_MODEL,
        "prompt_ver": config.PROMPT_VERSION,
        "path": entry["path"],
        "role": role,
        "summary": summary,
        "key_entities": entities,
    })
    # token 只统计真实发出的请求；缓存命中根本不会走到这里
    stats["tokens_in"] += result.tokens_in
    stats["tokens_out"] += result.tokens_out


def _read_source(path: Path) -> str:
    # 和 P0 extract_imports 同一套读法（utf-8-sig），两个阶段看到的文件内容才一致
    return path.read_text(encoding="utf-8-sig")


def _cache_key(source: str) -> str:
    """内容 + 提示词版本 + 模型 三样都进 hash：改任何一样缓存自动失效，
    避免"改了提示词却全部命中"这种最耽误调试的假象。"""
    raw = f"{config.PROMPT_VERSION}|{config.GLM_MODEL}|{source}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _apply(entry: dict, role: str | None, summary: str | None,
           key_entities: list[str] | None, cached: bool) -> None:
    entry["role"] = role
    entry["summary"] = summary
    entry["key_entities"] = key_entities
    entry["cached"] = cached


def _parse_result(content: str) -> tuple[str, str, list[str]]:
    """把模型输出拆成 (role, summary, key_entities)；不合法时抛 ValueError。"""
    text = content.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("返回内容里找不到 JSON 对象")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败: {exc.msg}（第 {exc.lineno} 行）") from exc
    if not isinstance(data, dict):
        raise ValueError("顶层不是 JSON 对象")
    role, summary = data.get("role"), data.get("summary")
    if not isinstance(role, str) or not isinstance(summary, str):
        raise ValueError("缺少 role/summary 字段")
    entities = data.get("key_entities") or []
    if not isinstance(entities, list):
        entities = [str(entities)]
    return role, summary, [str(e) for e in entities][:5]
