"""这一组在验：SSE 进度流的行为契约——快照先行、终态早退、事件转发、心跳、断开必退订。

每条用例都用 `isolated_store` 拿一张私有任务表（见 conftest），不碰模块级单例。
取事件一律用 `next(gen)` 逐条取；取完必须 close，否则测不到 finally 里的退订。
"""

import json
import queue
import threading
import time

import pytest

from api import routes
from services import task_service


def _post(client, payload):
    return client.post("/api/analyze", json=payload, content_type="application/json")


def _wait_status(client, task_id, wanted=("done", "failed")):
    """有界轮询到期望状态，绝不用固定 sleep 赌线程速度。"""
    for _ in range(500):
        data = client.get(f"/api/tasks/{task_id}").get_json()["data"]
        if data["status"] in wanted:
            return data
        time.sleep(0.01)
    raise AssertionError(f"任务 {task_id} 未在 5 秒内进入 {wanted}")


def _frame(raw) -> str:
    """生成器可能给出 str 或已编码的 bytes，统一还原成文本再断言。"""
    return raw.decode("utf-8") if isinstance(raw, bytes) else raw


def _event(raw) -> dict:
    """把一条 SSE 帧拆回 payload，顺带断言帧格式（少了结尾两个换行浏览器不触发 onmessage）。"""
    raw = _frame(raw)
    assert raw.startswith("data: ") and raw.endswith("\n\n")
    return json.loads(raw[len("data: "):-2])


def _push_to_subscriber(store, task_id, event):
    """直接往那条流的订阅队列里塞一条事件，用来证明终态之后它一条都不会被转发。"""
    store._tasks[task_id]["subscribers"][0].put_nowait(event)


def test_stream_response_declares_sse_headers(web_client, isolated_store, tmp_repo):
    """响应头三条：mimetype 是 event-stream、缓存关掉、代理缓冲关掉。"""
    task_id = isolated_store.submit(str(tmp_repo), False)
    _wait_status(web_client, task_id)

    resp = web_client.get(f"/api/tasks/{task_id}/stream")
    try:
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"
        assert resp.headers["Cache-Control"] == "no-cache"
        assert resp.headers["X-Accel-Buffering"] == "no"
    finally:
        resp.close()


def test_done_task_streams_snapshot_then_done_and_returns(web_client, isolated_store, tmp_repo):
    """终态早退：已 done 的任务只推「快照 + done」两条，生成器自己结束。"""
    task_id = isolated_store.submit(str(tmp_repo), False)
    _wait_status(web_client, task_id)

    resp = web_client.get(f"/api/tasks/{task_id}/stream")
    gen = resp.response
    try:
        snapshot = _event(next(gen))
        assert snapshot["type"] == "snapshot"
        assert snapshot["status"] == "done"
        assert snapshot["progress"] == {"done": 3, "total": 3}

        assert _event(next(gen)) == {"type": "done"}
        # 终态之后哪怕队列里还有事件也不许再转发：靠这条塞进去的进度证明「早退」真的退了
        _push_to_subscriber(isolated_store, task_id,
                            {"type": "progress", "done": 9, "total": 9, "path": "late.py"})
        with pytest.raises(StopIteration):
            next(gen)   # 早退分支：没有第三条，也不会挂着等事件
    finally:
        gen.close()

    assert isolated_store._tasks[task_id]["subscribers"] == []


def test_failed_task_streams_snapshot_then_failed_with_error(web_client, isolated_store, tmp_repo, monkeypatch):
    """failed 任务的早退事件必须带 error：前端靠它显示失败原因，而不是无限重连。"""
    def boom(repo):
        raise ValueError("路径给错了")

    monkeypatch.setattr(task_service, "analyze", boom)
    task_id = isolated_store.submit(str(tmp_repo), False)
    failed = _wait_status(web_client, task_id)
    assert failed["status"] == "failed" and failed["error"].startswith("[1001]")

    resp = web_client.get(f"/api/tasks/{task_id}/stream")
    gen = resp.response
    try:
        snapshot = _event(next(gen))
        assert snapshot["type"] == "snapshot" and snapshot["status"] == "failed"

        event = _event(next(gen))
        assert event["type"] == "failed"
        assert event["error"] == failed["error"]
        _push_to_subscriber(isolated_store, task_id,
                            {"type": "progress", "done": 1, "total": 3, "path": "late.py"})
        with pytest.raises(StopIteration):
            next(gen)
    finally:
        gen.close()


def test_running_task_forwards_published_events_until_terminal(web_client, isolated_store, tmp_repo, monkeypatch):
    """运行中的任务：快照后逐条转发 progress，收到 failed 事件就结束并退订。"""
    gate = threading.Event()

    def blocked_analyze(repo):
        gate.wait(5)   # 让任务停在 running，事件由测试自己投递
        return {"root": str(tmp_repo), "files": [], "edges": []}

    monkeypatch.setattr(task_service, "analyze", blocked_analyze)
    task_id = isolated_store.submit(str(tmp_repo), False)
    _wait_status(web_client, task_id, wanted=("running",))

    resp = web_client.get(f"/api/tasks/{task_id}/stream")
    gen = resp.response
    try:
        assert _event(next(gen))["status"] == "running"

        isolated_store._publish(task_id, {"type": "progress", "done": 1, "total": 2, "path": "a.py"})
        assert _event(next(gen)) == {"type": "progress", "done": 1, "total": 2, "path": "a.py"}

        isolated_store._publish(task_id, {"type": "failed", "error": "[9999] 意外"})
        assert _event(next(gen)) == {"type": "failed", "error": "[9999] 意外"}
        with pytest.raises(StopIteration):
            next(gen)
    finally:
        gen.close()
        gate.set()   # 放后台线程收工，别把阻塞线程留给下一条用例

    assert isolated_store._tasks[task_id]["subscribers"] == []


class FakeClock:
    """time.monotonic 的替身：每问一次前进 8 秒，两轮就跨过 15 秒心跳门槛。"""

    def __init__(self, step: float = 8.0):
        self._now = 0.0
        self._step = step

    def monotonic(self) -> float:
        self._now += self._step
        return self._now


class SilentQueue:
    """永远没有事件的订阅者队列：跳过真实 q.get(timeout=1) 的 1 秒等待。"""

    def get(self, timeout=None):
        raise queue.Empty

    def put_nowait(self, item):
        raise queue.Full


class FakeQueueModule:
    """替代 task_service.queue：subscribe 造出来的队列立即 Empty，让心跳分支可测。"""

    Empty = queue.Empty
    Full = queue.Full

    @staticmethod
    def Queue(maxsize=0):
        return SilentQueue()


def test_idle_stream_emits_heartbeat_event_with_fake_clock(web_client, isolated_store, tmp_repo, monkeypatch):
    """空闲 15 秒发一条 `data:` 的 heartbeat 真事件（不是注释行），假时钟验证，不真的 sleep 15 秒。"""
    monkeypatch.setattr(routes, "time", FakeClock())
    monkeypatch.setattr(task_service, "queue", FakeQueueModule)

    gate = threading.Event()

    def blocked_analyze(repo):
        gate.wait(5)
        return {"root": str(tmp_repo), "files": [], "edges": []}

    monkeypatch.setattr(task_service, "analyze", blocked_analyze)
    task_id = isolated_store.submit(str(tmp_repo), False)
    _wait_status(web_client, task_id, wanted=("running",))

    resp = web_client.get(f"/api/tasks/{task_id}/stream")
    gen = resp.response
    try:
        assert _event(next(gen))["status"] == "running"
        first = _frame(next(gen))
        assert first.startswith("data: ")
        assert _event(first) == {"type": "heartbeat"}
        # 第二条心跳证明发完心跳后计时被重置（否则会变成每秒一条）
        assert _event(next(gen)) == {"type": "heartbeat"}
    finally:
        gen.close()
        gate.set()


def test_heartbeat_is_a_plain_unnamed_sse_event():
    """契约：心跳必须走默认事件（帧里没有 `event:` 字段），前端唯一的 onmessage 才收得到。

    写成 `event: heartbeat` 的具名事件时，curl 和 devtools 看起来都正常，但浏览器
    不会触发 onmessage → 看门狗永远不续期，正是 P6 要消灭的那个缺口。
    """
    frame = routes._format_event({"type": "heartbeat"})
    assert frame == 'data: {"type": "heartbeat"}\n\n'
    assert "event:" not in frame
    assert _event(frame) == {"type": "heartbeat"}


class ScriptedQueue:
    """先 Empty 若干次、再给一条终态事件的队列：把「心跳之后仍能退出」压成同步序列。"""

    def __init__(self, empties_before_terminal: int):
        self._empties = empties_before_terminal

    def get(self, timeout=None):
        if self._empties > 0:
            self._empties -= 1
            raise queue.Empty
        return {"type": "done"}

    def put_nowait(self, item):
        raise queue.Full


class ScriptedQueueModule:
    Empty = queue.Empty
    Full = queue.Full

    def __init__(self, empties_before_terminal: int):
        self._empties = empties_before_terminal

    def Queue(self, maxsize=0):
        return ScriptedQueue(self._empties)


def test_heartbeats_never_prevent_the_stream_from_ending(web_client, isolated_store, tmp_repo, monkeypatch):
    """心跳只是续期：两条 heartbeat 之后收到 done，生成器仍要立刻结束（不挂连接）。"""
    monkeypatch.setattr(routes, "time", FakeClock())
    monkeypatch.setattr(task_service, "queue", ScriptedQueueModule(4))

    gate = threading.Event()

    def blocked_analyze(repo):
        gate.wait(5)
        return {"root": str(tmp_repo), "files": [], "edges": []}

    monkeypatch.setattr(task_service, "analyze", blocked_analyze)
    task_id = isolated_store.submit(str(tmp_repo), False)
    _wait_status(web_client, task_id, wanted=("running",))

    resp = web_client.get(f"/api/tasks/{task_id}/stream")
    gen = resp.response
    try:
        types = [_event(next(gen))["type"] for _ in range(4)]
        assert types == ["snapshot", "heartbeat", "heartbeat", "done"]
        with pytest.raises(StopIteration):
            next(gen)
    finally:
        gen.close()
        gate.set()

    assert isolated_store._tasks[task_id]["subscribers"] == []


def test_closing_the_stream_unsubscribes_the_queue(web_client, isolated_store, tmp_repo):
    """客户端断开就退订：订阅者列表只增不减会把内存和线程一起拖死。"""
    task_id = isolated_store.submit(str(tmp_repo), False)
    _wait_status(web_client, task_id)

    resp = web_client.get(f"/api/tasks/{task_id}/stream")
    gen = resp.response
    try:
        assert _event(next(gen))["type"] == "snapshot"
        assert len(isolated_store._tasks[task_id]["subscribers"]) == 1
    finally:
        gen.close()

    assert isolated_store._tasks[task_id]["subscribers"] == []


def test_stream_of_unknown_task_is_404_and_never_opens_a_stream(web_client, isolated_store):
    """未知任务在挂流之前就 404：错误也要走统一三字段壳，且绝不注册订阅者。"""
    resp = web_client.get("/api/tasks/no-such-task/stream")
    assert resp.status_code == 404
    assert resp.get_json() == {"code": 1003, "message": "分析任务不存在", "data": None}
    assert resp.mimetype != "text/event-stream"
    assert isolated_store._tasks == {}


def test_format_event_wraps_payload_as_one_sse_frame():
    """`_format_event` 是整条流唯一的报文出口：data 前缀 + 两换行 + 中文不转义。"""
    assert routes._format_event({"type": "done"}) == 'data: {"type": "done"}\n\n'
    assert "假的模块" in routes._format_event({"path": "假的模块.py"})
