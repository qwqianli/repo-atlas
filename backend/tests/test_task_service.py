"""这一组在验：任务表的线程契约——四类异常都收进 failed、进度双写、淘汰不碰活跃任务、队列满不堵线程。

全部自己 `TaskStore()` 实例化，不碰 task_service 模块级单例；失败分支直接同步调
`_run_task`，不依赖后台线程快慢。
"""

import queue

import pytest

from responses import BusinessError, ERROR_TASK_NOT_FOUND
from services import task_service
from services.task_service import TaskStore, _repo_name


def _task(task_id: str, status: str = "done", created_at: str = "2026-01-01T00:00:00",
          repo: str = "C:/tmp/repo") -> dict:
    """和 TaskStore.submit 造出来的结构一致，缺的只是「跑起来」这件事。"""
    return {
        "task_id": task_id,
        "repo": repo,
        "name": "repo",
        "with_ai": False,
        "status": status,
        "progress": {"done": 0, "total": 0},
        "created_at": created_at,
        "result": None,
        "error": None,
        "subscribers": [],
    }


@pytest.mark.parametrize(
    "exc, expected_prefix",
    [
        (ValueError("路径不对"), "[1001]"),
        (task_service.LLMError("API Key 无效", fatal=True), "[1002]"),
        (BusinessError(ERROR_TASK_NOT_FOUND, "任务不存在", http_status=404), "[1003]"),
        (RuntimeError("谁也没想到的意外"), "[9999]"),
    ],
)
def test_any_worker_exception_lands_in_failed_with_its_code(monkeypatch, exc, expected_prefix):
    """四个 except 分支是最后一道防线：错了任务就永远 running，前端只能干等。"""
    store = TaskStore()

    def boom(repo):
        raise exc

    monkeypatch.setattr(task_service, "analyze", boom)
    task = _task("t-fail", status="pending")

    store._run_task(task)

    assert task["status"] == "failed"
    assert task["error"].startswith(expected_prefix + " ")   # f"[{exc.code}] {exc.message}"
    assert task["result"] is None


def test_ai_task_prefills_total_before_any_progress(monkeypatch, tmp_path):
    """进 AI 阶段前 total 就要等于文件数：页面显示「0 / N」而不是「0 / ?」。"""
    store = TaskStore()
    analysis = {
        "root": str(tmp_path),
        "files": [{"path": "a.py"}, {"path": "b.py"}],
        "edges": [],
    }
    monkeypatch.setattr(task_service, "analyze", lambda repo: analysis)

    seen = {}

    def fake_summarize(analysis, root, on_progress):
        seen["progress_when_ai_starts"] = dict(task["progress"])
        on_progress(1, 2, "a.py")
        on_progress(2, 2, "b.py")

    monkeypatch.setattr(task_service, "summarize_files", fake_summarize)
    task = _task("t-ai", status="pending")
    task["with_ai"] = True

    store._run_task(task)

    assert seen["progress_when_ai_starts"] == {"done": 0, "total": 2}
    assert task["progress"] == {"done": 2, "total": 2}
    assert task["status"] == "done"
    assert task["result"]["reading_path"]["order"] == ["a.py", "b.py"]


def test_on_progress_writes_snapshot_and_broadcasts(monkeypatch):
    """进度双写：轮询读快照、SSE 读队列，两条路径看到同一份数字。"""
    store = TaskStore()
    task = _task("t-progress", status="running")
    store._tasks["t-progress"] = task
    q = store.subscribe("t-progress")

    store._on_progress(task, 2, 5, "pkg/a.py")

    assert task["progress"] == {"done": 2, "total": 5}
    assert q.get_nowait() == {"type": "progress", "done": 2, "total": 5, "path": "pkg/a.py"}


def test_eviction_deletes_oldest_finished_but_skips_live_subscribers(monkeypatch):
    """淘汰只动已结束任务；有订阅者的最老任务也要留到下一轮（72-73 的 continue）。"""
    monkeypatch.setattr(task_service, "MAX_TASKS", 3)
    store = TaskStore()
    oldest = _task("oldest", created_at="2026-01-01T00:00:00")
    oldest["subscribers"] = [queue.Queue()]   # SSE 还挂在上面
    older = _task("older", created_at="2026-01-02T00:00:00")
    newest = _task("newest", created_at="2026-01-03T00:00:00")
    running = _task("running", status="running", created_at="2026-01-04T00:00:00")
    for task in (oldest, older, newest, running):
        store._tasks[task["task_id"]] = task

    store._evict_finished_locked()

    assert "oldest" in store._tasks        # 有订阅者，最老也不删
    assert "older" not in store._tasks     # 真正该被淘汰的那条
    assert "newest" in store._tasks        # 删够数就停，不连带误删
    assert "running" in store._tasks       # 未结束的任务永远不删
    assert len(store._tasks) == 3


def test_subscribe_and_unsubscribe_keep_the_list_balanced():
    """订阅/退订必须成对；未知任务订阅给 1003，退订则静默容忍。"""
    store = TaskStore()
    store._tasks["s1"] = _task("s1", status="running")

    q1 = store.subscribe("s1")
    q2 = store.subscribe("s1")
    assert store._tasks["s1"]["subscribers"] == [q1, q2]

    store.unsubscribe("s1", q1)
    assert store._tasks["s1"]["subscribers"] == [q2]

    store.unsubscribe("no-such-task", q2)   # 任务已被淘汰时退订不能炸
    with pytest.raises(BusinessError) as exc_info:
        store.subscribe("no-such-task")
    assert exc_info.value.code == ERROR_TASK_NOT_FOUND


def test_full_subscriber_queue_drops_the_event_without_blocking():
    """订阅者不消费也不能堵死分析线程：队列满就丢这条，前端还有轮询兜底。"""
    store = TaskStore()
    store._tasks["f1"] = _task("f1", status="running")
    tiny = queue.Queue(maxsize=1)
    store._tasks["f1"]["subscribers"].append(tiny)
    tiny.put_nowait({"type": "first"})

    store._publish("f1", {"type": "second"})   # 满队列：静默丢弃，不抛异常

    assert tiny.get_nowait() == {"type": "first"}
    assert tiny.empty()


def test_repo_name_handles_git_urls_and_local_paths():
    """仓库名：http(s) 与 git@ 都取最后一段并去掉 .git；本地路径取目录名。"""
    assert _repo_name("https://github.com/lab/repo-atlas.git") == "repo-atlas"
    assert _repo_name("git@github.com:lab/repo-atlas.git") == "repo-atlas"
    assert _repo_name("https://github.com/lab/repo-atlas/") == "repo-atlas"
    assert _repo_name("C:/work/atlas") == "atlas"
