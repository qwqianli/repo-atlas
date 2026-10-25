"""内存任务表：提交立刻返回，分析在后台线程跑，进度通过 SSE 推给订阅者。

任务表是进程内 dict——重启即丢，这是刻意接受的简化（不上 Redis 不建表）。
表有上限 MAX_TASKS：每个任务的 result 有几百 KB，不设限内存只涨不降。
dict 的读改写加锁：Flask 开发服务器一个请求一个线程，不加锁会互相踩。
"""

import logging
import queue
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
from analyzer.reading_path import build_reading_path
from analyzer.summarizer import summarize_files

logger = logging.getLogger(__name__)

MAX_TASKS = 50          # 任务表上限：超出删最早创建的已结束任务
MAX_QUEUE_SIZE = 1000   # 每个 SSE 订阅者的事件队列上限：满了丢消息，绝不堵分析线程


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
            "subscribers": [],   # list[queue.Queue]，SSE 专用，不进接口返回体
        }
        with self._lock:
            self._tasks[task_id] = task
            self._evict_finished_locked()
        # daemon=True：主进程退出时后台线程跟着退，不挡关闭
        threading.Thread(target=self._run_task, args=(task,), daemon=True).start()
        return task_id

    def _evict_finished_locked(self) -> None:
        """调用方必须已持锁。只删已结束的任务；还有订阅者的跳过不删。"""
        if len(self._tasks) <= MAX_TASKS:
            return
        finished = sorted(
            (t for t in self._tasks.values() if t["status"] in ("done", "failed")),
            key=lambda t: t["created_at"],
        )
        for t in finished:
            if len(self._tasks) <= MAX_TASKS:
                break
            if t["subscribers"]:
                continue   # SSE 还挂在上面，删了会把连接晾死；留到下一轮清理
            del self._tasks[t["task_id"]]

    def get(self, task_id: str) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise BusinessError(ERROR_TASK_NOT_FOUND, "分析任务不存在", http_status=404)
            # subscribers 是内部实现（Queue 对象也没法 JSON 序列化），绝不能出现在接口里
            snapshot = {k: v for k, v in task.items() if k != "subscribers"}
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

    def subscribe(self, task_id: str) -> queue.Queue:
        """注册一个 SSE 订阅者，返回它专属的事件队列。"""
        q: queue.Queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise BusinessError(ERROR_TASK_NOT_FOUND, "分析任务不存在", http_status=404)
            task["subscribers"].append(q)
        return q

    def unsubscribe(self, task_id: str, q: queue.Queue) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None and q in task["subscribers"]:
                task["subscribers"].remove(q)

    def _publish(self, task_id: str, event: dict) -> None:
        # 遍历副本：subscribe/unsubscribe 在别的请求线程同时发生也不会炸
        with self._lock:
            subscribers = list(self._tasks.get(task_id, {}).get("subscribers", []))
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                # 订阅者不消费也不能堵死分析线程：丢这条，前端还有轮询兜底
                pass

    def _run_task(self, task: dict) -> None:
        task["status"] = "running"
        task_id = task["task_id"]
        try:
            # 复用分析层的总入口，Web 层绝不重写分析逻辑
            analysis = analyze(task["repo"])
            # 文件数在解析完就确定了：先把 total 填上，
            # 页面一进来显示的就是「0 / N」而不是「0 / ?」
            task["progress"] = {"done": 0, "total": len(analysis["files"])}
            if task["with_ai"]:
                summarize_files(
                    analysis, Path(analysis["root"]),
                    on_progress=lambda done, total, path: self._on_progress(task, done, total, path),
                )
            # 阅读路线只由算法从依赖图算出：AI 不参与顺序决策，结果才可复现、可验证
            analysis["reading_path"] = build_reading_path(analysis["files"], analysis["edges"])
            task["result"] = analysis
            # 不带 AI 的任务没有 on_progress，收尾把进度补成满格
            task["progress"] = {"done": len(analysis["files"]), "total": len(analysis["files"])}
            task["status"] = "done"
            self._publish(task_id, {"type": "done"})
        except ValueError as exc:
            self._finish_failed(task, BusinessError(ERROR_REPO_INVALID, f"仓库路径无效: {exc}"))
        except LLMError as exc:
            self._finish_failed(task, BusinessError(ERROR_NEED_KEY, f"AI 说明生成失败: {exc}"))
        except BusinessError as exc:
            self._finish_failed(task, exc)
        except Exception as exc:
            # 后台线程是最后一道防线：把任何意外收进 task.error，否则线程一死任务永远 running
            logger.exception("分析任务 %s 失败", task_id)
            self._finish_failed(task, BusinessError(ERROR_INTERNAL, f"内部错误: {exc}"))

    def _on_progress(self, task: dict, done: int, total: int, path: str) -> None:
        # 两件事都要做：写进任务快照（轮询兜底路径靠它显示进度）+ 广播（SSE 主路径）
        task["progress"] = {"done": done, "total": total}
        self._publish(task["task_id"], {"type": "progress", "done": done, "total": total, "path": path})

    def _finish_failed(self, task: dict, exc: BusinessError) -> None:
        # 先写 error 再写 status，最后才广播：读侧永远看不到 failed 但 error 为空
        task["error"] = f"[{exc.code}] {exc.message}"
        task["status"] = "failed"
        self._publish(task["task_id"], {"type": "failed", "error": task["error"]})


def _repo_name(repo: str) -> str:
    """本地路径取最后一段；git 地址取仓库名并去掉 .git 后缀。"""
    if repo.startswith(("http://", "https://", "git@")):
        name = repo.rstrip("/").rsplit("/", 1)[-1]
        return name[:-4] if name.endswith(".git") else name
    return Path(repo).name or repo


# 模块级单例：routes 和 app 共用同一张任务表
store = TaskStore()
