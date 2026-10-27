"""这一组在验：三个 HTTP 接口的响应壳、错误码，以及内部实现字段绝不泄漏到响应体。"""

import time

import config

from app import create_app


def _post(client, payload):
    return client.post(
        "/api/analyze", json=payload, content_type="application/json"
    )


def test_analyze_returns_task_id_immediately(tmp_repo):
    app = create_app()
    client = app.test_client()
    resp = _post(client, {"repo": str(tmp_repo), "with_ai": False})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 0
    assert set(body["data"]) == {"task_id"}


def test_unknown_task_returns_1003_with_http_404():
    client = create_app().test_client()
    resp = client.get("/api/tasks/no-such-id")
    assert resp.status_code == 404
    assert resp.get_json()["code"] == 1003


def test_empty_repo_returns_1001_with_http_400():
    client = create_app().test_client()
    resp = _post(client, {"repo": "   ", "with_ai": False})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == 1001


def test_missing_repo_field_returns_1001():
    client = create_app().test_client()
    resp = _post(client, {"with_ai": False})
    assert resp.get_json()["code"] == 1001


def test_nonexistent_path_returns_1001():
    client = create_app().test_client()
    resp = _post(client, {"repo": "C:/definitely/not/here", "with_ai": False})
    assert resp.get_json()["code"] == 1001


def test_every_response_uses_the_three_field_envelope(tmp_repo):
    """统一响应壳：任何接口的任何分支都是 {code, message, data} 三字段。"""
    client = create_app().test_client()
    for resp in (
        _post(client, {"repo": str(tmp_repo), "with_ai": False}),
        client.get("/api/tasks/no-such-id"),
        client.get("/api/repos"),
    ):
        assert set(resp.get_json()) == {"code", "message", "data"}


def test_task_snapshot_hides_subscribers_from_the_api(tmp_repo):
    """subscribers 里是不可序列化的 Queue 对象，一旦泄漏进响应体就是 500。"""
    client = create_app().test_client()
    task_id = _post(client, {"repo": str(tmp_repo), "with_ai": False}).get_json()["data"]["task_id"]
    # 后台线程的分析在毫秒级完成，但保险起见等它到终态
    for _ in range(50):
        body = client.get(f"/api/tasks/{task_id}").get_json()
        if body["data"]["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    raw = str(body)
    assert body["data"]["status"] == "done"
    assert "subscribers" not in raw and "queue" not in raw.lower()
    assert "reading_path" in body["data"]["result"]   # 阅读路线要进结果


def test_repos_lists_finished_analysis(tmp_repo):
    client = create_app().test_client()
    _post(client, {"repo": str(tmp_repo), "with_ai": False})
    resp = client.get("/api/repos")
    items = resp.get_json()["data"]
    assert len(items) >= 1
    assert {"name", "repo", "task_id", "last_run", "status"} <= set(items[0])


# ---------------- Web 层 AI 主路径、缺 Key 分支、全局兜底 ----------------


def _wait_terminal(client, task_id):
    """有界轮询到终态，不用固定 sleep 赌后台线程的速度。"""
    for _ in range(500):
        data = client.get(f"/api/tasks/{task_id}").get_json()["data"]
        if data["status"] in ("done", "failed"):
            return data
        time.sleep(0.01)
    raise AssertionError(f"任务 {task_id} 未在 5 秒内到终态")


def test_analyze_with_ai_runs_fake_llm_and_returns_ai_fields(
    tmp_repo, fake_llm, isolated_store, monkeypatch, tmp_path
):
    """with_ai=True 是产品主路径：走 summarize_files + on_progress，结果带 AI 字段。

    缓存库必须挪到 tmp_path：config.DB_PATH 与生产共用同一套默认键空间，
    不隔离就会把假摘要静默写进 backend/data/atlas.db。
    """
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "p5.db")
    monkeypatch.setattr(config, "GLM_API_KEY", "test-key")
    client = create_app().test_client()

    task_id = _post(client, {"repo": str(tmp_repo), "with_ai": True}).get_json()["data"]["task_id"]
    data = _wait_terminal(client, task_id)

    assert data["status"] == "done"
    assert data["progress"] == {"done": 3, "total": 3}
    assert fake_llm.calls == 3
    assert data["result"]["ai_stats"]["miss"] == 3
    assert all(f["role"] == "假的角色说明" for f in data["result"]["files"])
    assert (tmp_path / "p5.db").exists()   # 摘要落在隔离库里，不在真库里


def test_with_ai_without_a_key_fails_before_any_task_is_created(tmp_repo, isolated_store, monkeypatch):
    """缺 Key 属于提交前校验：1002 + 400，任务表里不该留下半截任务。"""
    monkeypatch.setattr(config, "GLM_API_KEY", "")
    client = create_app().test_client()

    resp = _post(client, {"repo": str(tmp_repo), "with_ai": True})

    assert resp.status_code == 400
    assert resp.get_json()["code"] == 1002
    assert isolated_store._tasks == {}


def test_unknown_route_keeps_flask_404_instead_of_becoming_500():
    """全局兜底不能把普通 404 误报成 500——否则监控里全是假故障。"""
    client = create_app().test_client()
    resp = client.get("/api/definitely-not-a-route")
    assert resp.status_code == 404
    assert b"9999" not in resp.data


def test_unexpected_exception_returns_500_with_three_field_envelope():
    """未预期异常统一收成 {code, message, data} + 500，而不是裸抛 traceback。"""
    app = create_app()

    def boom():
        raise RuntimeError("未预期的内部异常")

    app.add_url_rule("/boom", "boom", boom)
    resp = app.test_client().get("/boom")

    assert resp.status_code == 500
    body = resp.get_json()
    assert set(body) == {"code", "message", "data"}
    assert body["code"] == 9999
    assert body["data"] is None
