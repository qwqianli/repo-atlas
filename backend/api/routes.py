"""HTTP 接口。路由层只做参数校验和转发，异常统一抛 BusinessError。"""

import json
import queue
import time
from pathlib import Path

from flask import Blueprint, Response, request, stream_with_context

import config
from responses import BusinessError, ok, ERROR_NEED_KEY, ERROR_REPO_INVALID
from services.task_service import store

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.post("/analyze")
def start_analyze():
    body = request.get_json(silent=True) or {}
    repo = str(body.get("repo") or "").strip()
    if not repo:
        raise BusinessError(ERROR_REPO_INVALID, "仓库路径不能为空")
    with_ai = bool(body.get("with_ai", True))
    # 本地路径在提交前就同步校验：手滑打错路径能立刻拿到 400，而不是等任务失败才知道
    if not repo.startswith(("http://", "https://", "git@")) and not Path(repo).is_dir():
        raise BusinessError(ERROR_REPO_INVALID, "仓库路径不存在或不是目录")
    if with_ai:
        try:
            config.require_api_key()
        except RuntimeError as exc:
            raise BusinessError(ERROR_NEED_KEY, str(exc)) from exc
    task_id = store.submit(repo, with_ai)
    return ok({"task_id": task_id})


@api_bp.get("/tasks/<task_id>")
def get_task(task_id: str):
    return ok(store.get(task_id))


@api_bp.get("/tasks/<task_id>/stream")
def stream_task(task_id: str):
    """SSE 进度流：浏览器用 EventSource，命令行用 curl -N 验证。"""
    q = store.subscribe(task_id)   # 任务不存在在这里就抛 1003，不会挂成一条死流

    def gen():
        try:
            # 连上先推快照：刷新页面立刻能看到当前状态，不用干等下一个事件
            task = store.get(task_id)
            yield _format_event({
                "type": "snapshot",
                "status": task["status"],
                "progress": task["progress"],
            })
            if task["status"] in ("done", "failed"):
                event = {"type": task["status"]}
                if task["status"] == "failed":
                    event["error"] = task["error"]
                yield _format_event(event)
                return   # 已结束的任务推完就走，生成器绝不能挂着占线程
            last_emit = time.monotonic()
            while True:
                try:
                    # 必须带超时：任务异常结束后没有新事件，无超时阻塞会让生成器永久挂住
                    event = q.get(timeout=1)
                except queue.Empty:
                    # 超过 15 秒没事件就发一条 heartbeat 真事件：注释行不触发前端 onmessage，
                    # 前端看门狗靠它续期；浏览器忽略内容，但代理不会掐断空闲连接
                    if time.monotonic() - last_emit >= 15:
                        yield _format_event({"type": "heartbeat"})
                        last_emit = time.monotonic()
                    continue
                yield _format_event(event)
                last_emit = time.monotonic()
                if event.get("type") in ("done", "failed"):
                    return
        finally:
            store.unsubscribe(task_id, q)   # 不注销的话订阅者列表只增不减

    response = Response(stream_with_context(gen()), mimetype="text/event-stream")
    # X-Accel-Buffering 关掉 Nginx 类代理的响应缓冲，否则进度会攒到最后一次性吐出
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


def _format_event(payload: dict) -> str:
    # SSE 一条消息以两个换行结尾；少一个浏览器不触发 onmessage，页面看着连上了但永远没数据
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@api_bp.get("/repos")
def list_repos():
    """任务列表按仓库聚合：一个仓库一条，取最近一次运行的状态和时间。"""
    repos: dict[str, dict] = {}
    for task in store.list_recent():
        if task["repo"] not in repos:
            repos[task["repo"]] = {
                "name": task["name"],
                "repo": task["repo"],
                "task_id": task["task_id"],   # 前端历史列表要能点回结果页
                "last_run": task["created_at"],
                "status": task["status"],
            }
    return ok(list(repos.values()))
