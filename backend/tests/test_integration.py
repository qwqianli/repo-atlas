"""这一组在验：端到端离线跑通——解析、假 AI、缓存二连击、阅读路线，以及 LLM 客户端的重试契约。"""

import json
from pathlib import Path

import pytest

import config
from analyzer import llm_client
from analyzer.cli import analyze, render_text
from analyzer.llm_client import LLMError, LLMResult
from analyzer.reading_path import build_reading_path
from analyzer.summarizer import summarize_files


def _summarize_with_tmp_db(analysis, monkeypatch, tmp_path, repo_root):
    """按 web 层（task_service）同样的顺序组合：summarize + 阅读路线注入。"""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "it.db")
    summarize_files(analysis, Path(repo_root))
    analysis["reading_path"] = build_reading_path(analysis["files"], analysis["edges"])


def test_full_analysis_with_fake_llm(rel_repo, fake_llm, monkeypatch, tmp_path):
    """离线全链路：静态解析 + 假 AI 说明 + 阅读路线，一次跑完。"""
    analysis = analyze(str(rel_repo))
    _summarize_with_tmp_db(analysis, monkeypatch, tmp_path, analysis["root"])

    assert len(analysis["files"]) == 7
    assert len(analysis["edges"]) == 9
    for entry in analysis["files"]:
        assert entry["role"] == "假的角色说明"
        assert entry["summary"]
        assert entry["cached"] is False
    assert fake_llm.calls == 7   # 7 个有效文件，每个恰好一次请求

    path = analysis["reading_path"]
    assert path["order"][0] in ("pkg/sub/sibling.py", "pkg/util.py")
    assert path["cycles"] == []


def test_second_run_hits_cache_with_zero_tokens(rel_repo, fake_llm, monkeypatch, tmp_path):
    """缓存的核心承诺：第二遍零请求、零 token、毫秒级。"""
    for _ in range(2):
        analysis = analyze(str(rel_repo))
        _summarize_with_tmp_db(analysis, monkeypatch, tmp_path, analysis["root"])
    stats = analysis["ai_stats"]
    assert stats["hit"] == 7 and stats["miss"] == 0
    assert stats["tokens_in"] == 0 and stats["tokens_out"] == 0
    assert fake_llm.calls == 7   # 第二轮一次新请求都没发
    assert all(f["cached"] for f in analysis["files"])


def test_reading_path_present_after_summarize(rel_repo, fake_llm, monkeypatch, tmp_path):
    analysis = analyze(str(rel_repo))
    _summarize_with_tmp_db(analysis, monkeypatch, tmp_path, analysis["root"])
    assert "reading_path" in analysis
    assert set(analysis["reading_path"]["order"]) == {f["path"] for f in analysis["files"]}


def test_render_text_still_works_on_ai_result(rel_repo, fake_llm, monkeypatch, tmp_path):
    analysis = analyze(str(rel_repo))
    _summarize_with_tmp_db(analysis, monkeypatch, tmp_path, analysis["root"])
    text = render_text(analysis)
    assert "文件清单" in text and "项目内依赖" in text
    assert "假的角色说明" in text


def test_bad_llm_output_lands_in_ai_errors_without_crashing(rel_repo, monkeypatch, tmp_path):
    """模型没按约定回 JSON：记进 ai_errors、字段置 None，流程不中断。"""

    def rude_chat(system, user):
        return LLMResult(content="我才不回 JSON 呢", tokens_in=5, tokens_out=5)

    monkeypatch.setattr(llm_client, "chat", rude_chat)
    analysis = analyze(str(rel_repo))
    _summarize_with_tmp_db(analysis, monkeypatch, tmp_path, analysis["root"])
    assert analysis["ai_stats"]["failed"] == 7
    assert len(analysis["ai_errors"]) == 7
    assert all(f["role"] is None for f in analysis["files"])


def test_fatal_llm_error_actually_raises(rel_repo, monkeypatch, tmp_path):
    """Key 无效这类致命错要整体抛出（cli/web 层据此退出），不能挨个失败 7 次。"""
    def broken_key_chat(system, user):
        raise LLMError("API Key 无效（HTTP 401）", fatal=True)

    monkeypatch.setattr(llm_client, "chat", broken_key_chat)
    analysis = analyze(str(rel_repo))
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "it.db")
    with pytest.raises(LLMError) as exc_info:
        summarize_files(analysis, Path(analysis["root"]))
    assert exc_info.value.fatal is True


# ---------------- LLM 客户端的离线契约（不发任何网络请求） ----------------


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.ok = status_code < 300
        self.payload = payload if payload is not None else {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        self.text = json.dumps(self.payload, ensure_ascii=False)

    def json(self):
        return self.payload


def _install_post(monkeypatch, responses, sleep_log=None):
    """把 llm_client.requests.post 换成按脚本回放的假实现。"""
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[i]

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    if sleep_log is not None:
        monkeypatch.setattr(llm_client.time, "sleep", lambda s: sleep_log.append(s))
    else:
        monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
    return calls


def test_chat_parses_a_successful_response(monkeypatch):
    calls = _install_post(monkeypatch, [FakeResponse()])
    result = llm_client.chat("sys", "user")
    assert result.content == "ok"
    assert result.tokens_in == 10 and result.tokens_out == 5
    assert calls["n"] == 1


def test_chat_retries_on_1305_then_succeeds(monkeypatch):
    """限流类业务码要退避重试，重试的间隔是 2s / 4s 递增。"""
    sleeps = []
    body = {"error": {"code": "1305", "message": "访问量过大"}}
    _install_post(monkeypatch, [FakeResponse(429, body), FakeResponse()], sleeps)
    result = llm_client.chat("sys", "user")
    assert result.content == "ok"
    # 列表里可能混着节流 sleep（<1s），只认真正的重试退避 2s
    assert [s for s in sleeps if s >= 1] == [2]


def test_chat_treats_401_as_fatal_without_retry(monkeypatch):
    calls = _install_post(monkeypatch, [FakeResponse(401)])
    with pytest.raises(LLMError) as exc_info:
        llm_client.chat("sys", "user")
    assert exc_info.value.fatal is True
    assert calls["n"] == 1   # Key 的问题重试也没用，一次都不重试


def test_chat_reports_1308_with_flush_time_and_never_retries(monkeypatch):
    calls = _install_post(
        monkeypatch,
        [FakeResponse(429, {"error": {"code": "1308", "message": "配额窗口",
                                      "next_flush_time": "12:00:05"}})],
    )
    with pytest.raises(LLMError) as exc_info:
        llm_client.chat("sys", "user")
    assert "1308" in str(exc_info.value)
    assert "12:00:05" in str(exc_info.value)
    assert calls["n"] == 1


def test_chat_url_appends_the_chat_path_for_base_addresses():
    assert llm_client._chat_url("https://x/api/paas/v4") == "https://x/api/paas/v4/chat/completions"
    assert (llm_client._chat_url("https://x/api/paas/v4/chat/completions/")
            == "https://x/api/paas/v4/chat/completions")


# ---------------- 提示词的代码截断 ----------------


def test_truncate_source_keeps_imports_and_signatures_drops_bodies():
    """超限文件只保留 docstring、import 区、def/class 签名行，并明确标注已截断。"""
    code = (
        '"""模块说明。"""\n'
        "import os\n"
        "from pathlib import Path\n"
        "\n"
        "def load(name):\n"
        "    very_long_body_line_that_should_disappear = name * 100\n"
        "    return very_long_body_line_that_should_disappear\n"
        "\n"
        "class Repo:\n"
        "    def scan(self):\n"
        "        return [1, 2, 3]\n"
    )
    from analyzer.prompts import truncate_source

    out = truncate_source(code, limit=120)
    assert "import os" in out
    assert "def load(name):" in out
    assert "class Repo:" in out
    assert "should_disappear" not in out   # 函数体被丢弃
    assert "# ... 已截断 ..." in out


def test_truncate_source_leaves_short_files_untouched():
    from analyzer.prompts import truncate_source

    code = "x = 1\n"
    assert truncate_source(code, limit=100) == code


# ---------------- CLI 入口 ----------------


def test_cli_main_prints_text_report_and_exits_zero(rel_repo, capsys):
    from analyzer.cli import main

    assert main([str(rel_repo)]) == 0
    out = capsys.readouterr().out
    assert "文件清单" in out and "内部依赖: 9 条" in out


def test_cli_main_prints_json_when_asked(rel_repo, capsys):
    import json as json_module

    from analyzer.cli import main

    assert main([str(rel_repo), "--json"]) == 0
    payload = json_module.loads(capsys.readouterr().out)
    assert payload["root"].endswith("rel_imports")
    assert len(payload["files"]) == 7


def test_cli_main_returns_2_for_bad_path(capsys):
    from analyzer.cli import main

    assert main(["C:/definitely/not/here"]) == 2
    assert "错误" in capsys.readouterr().err
