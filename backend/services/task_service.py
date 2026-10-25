"""内存任务表：提交立刻返回，分析在后台线程跑。

任务表是进程内 dict——重启即丢，这是刻意接受的简化（不上 Redis 不建表）。
dict 的读改写加锁：Flask 开发服务器一个请求一个线程，不加锁会互相踩。
"""

import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path

from responses import (
    BusinessError,
    ERROR_INTERNAL,
    ERROR_NEED_KEY,
    ERROR_REPO_INVALID,
    ERROR_TASK_NOT_FOUND,
)

from analyzer.cli import analyze
from analyzer.llm_client import LLMError
from analyzer.summarizer import summarize_files

logger = logging.getLogger(__name__)


class TaskStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: dict[str, dict] = {}

    def submit(self, repo: str, with_ai: bool) -> str:
        """创建任务并立刻返回 task_id；耗时的分析交给后台守护线程。"""
        task_id = uuid.uuid4().hex[:8]
        task = {
            "task_id": task_id,
            "repo": repo,
            "name": _repo_name(repo),
            "with_ai": with_ai,
            "status": "pending",
            "progress": {"done": 0, "total": 0},
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "result": None,
            "error": None,
        }
        with self._lock:
            self._tasks[task_id] = task
        # daemon=True：主进程退出时后台线程跟着退，不挡关闭
        threading.Thread(target=self._run_task, args=(task,), daemon=True).start()
        return task_id

    def get(self, task_id: str) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise BusinessError(ERROR_TASK_NOT_FOUND, "分析任务不存在", http_status=404)
        # 复制两层再返回：调用方拿到的快照不会被后台线程的写穿
        snapshot = dict(task)
        snapshot["progress"] = dict(task["progress"])
        return snapshot

    def list_recent(self, limit: int = 20) -> list[dict]:
        """最近的任务，只带列表要用的字段——result 有几百 KB，绝不外带。"""
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda t: t["created_at"], reverse=True)
        return [
            {"task_id": t["task_id"], "repo": t["repo"], "name": t["name"],
             "status": t["status"], "created_at": t["created_at"]}
            for t in tasks[:limit]
        ]

    def _run_task(self, task: dict) -> None:
        task["status"] = "running"
        try:
            # 复用 P0/P1 的总入口，Web 层绝不重写分析逻辑
            analysis = analyze(task["repo"])
            if task["with_ai"]:
                summarize_files(
                    analysis, Path(analysis["root"]),
                    on_progress=lambda done, total, path: self._set_progress(task, done, total),
                )
            task["result"] = analysis
            # 不带 AI 的任务没有 on_progress，收尾时把进度补成满格
            task["progress"] = {"done": len(analysis["files"]), "total": len(analysis["files"])}
            task["status"] = "done"
        except ValueError as exc:
            _fail(task, BusinessError(ERROR_REPO_INVALID, f"仓库路径无效: {exc}"))
        except LLMError as exc:
            _fail(task, BusinessError(ERROR_NEED_KEY, f"AI 说明生成失败: {exc}"))
        except BusinessError as exc:
            _fail(task, exc)
        except Exception as exc:
            # 后台线程是最后一道防线：把任何意外收进 task.error，否则线程一死任务永远 running
            logger.exception("分析任务 %s 失败", task["task_id"])
            _fail(task, BusinessError(ERROR_INTERNAL, f"内部错误: {exc}"))

    def _set_progress(self, task: dict, done: int, total: int) -> None:
        # 单次赋值原子，进度这种允许瞬时不一致的数据不值得再上锁
        task["progress"] = {"done": done, "total": total}


def _repo_name(repo: str) -> str:
    """本地路径取最后一段；git 地址取仓库名并去掉 .git 后缀。"""
    if repo.startswith(("http://", "https://", "git@")):
        name = repo.rstrip("/").rsplit("/", 1)[-1]
        return name[:-4] if name.endswith(".git") else name
    return Path(repo).name or repo


def _fail(task: dict, exc: BusinessError) -> None:
    # 先写 error 再写 status：读侧永远不会看到 failed 但 error 还是 None
    task["error"] = f"[{exc.code}] {exc.message}"
    task["status"] = "failed"


# 模块级单例：routes 和 app 共用同一张任务表
store = TaskStore()
