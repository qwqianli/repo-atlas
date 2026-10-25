"""三个 HTTP 接口。路由层只做参数校验和转发，异常统一抛 BusinessError。"""

from pathlib import Path

from flask import Blueprint, request

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
