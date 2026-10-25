"""测试夹具：迷你仓库在临时目录里现造，AI 调用全部换成假的——离线、零 token、秒级完成。

注意：必须用 `python -m pytest` 从 backend/ 目录启动（`python -m` 会把当前目录加进
sys.path，analyzer / config 这些顶层导入才解析得到；裸 `pytest` 命令不会）。
"""

import json
from pathlib import Path

import pytest

from analyzer import llm_client

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def rel_repo() -> Path:
    """相对导入样本仓库（9 条边基线）。"""
    return FIXTURES / "rel_imports"


@pytest.fixture
def cycle_repo() -> Path:
    """a↔b 成环、c→a 单向依赖的样本仓库（环检测的黄金用例）。"""
    return FIXTURES / "cycle"


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """现造的三文件迷你仓库：top → mid → base（base 谁也不依赖）。测试不依赖仓库里已有的 fixture。"""
    (tmp_path / "base.py").write_text("BASE = 1\n", encoding="utf-8")
    (tmp_path / "mid.py").write_text("from base import BASE\n", encoding="utf-8")
    (tmp_path / "top.py").write_text("from mid import BASE\n", encoding="utf-8")
    return tmp_path


class FakeLLM:
    """llm_client.chat 的替身：永远返回一份合法的 JSON，并数着自己被调用了几次。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, system: str, user: str) -> llm_client.LLMResult:
        self.calls += 1
        content = json.dumps(
            {
                "role": "假的角色说明",
                "summary": "假的摘要，用于离线测试。",
                "key_entities": ["fake_func"],
            },
            ensure_ascii=False,
        )
        return llm_client.LLMResult(content=content, tokens_in=100, tokens_out=20)


@pytest.fixture
def fake_llm(monkeypatch):
    """换掉 llm_client.chat 之后，所有涉及 AI 的测试都不再发网络请求。"""
    fake = FakeLLM()
    monkeypatch.setattr(llm_client, "chat", fake)
    return fake
