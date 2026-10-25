"""这一组在验：三个 HTTP 接口的响应壳、错误码，以及内部实现字段绝不泄漏到响应体。"""

import time

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
