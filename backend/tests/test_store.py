"""这一组在验：SQLite 缓存的存取、缓存键对「内容+版本+模型」三要素的敏感度、配置值去空白。"""

import config
from analyzer.store import SummaryStore
from analyzer.summarizer import _cache_key


def _record(path="pkg/core.py"):
    return {
        "model": "glm-4.7-flash",
        "prompt_ver": "v1",
        "path": path,
        "role": "角色",
        "summary": "摘要",
        "key_entities": ["A", "b"],
    }


def test_put_then_get_returns_the_same_record(tmp_path):
    store = SummaryStore(tmp_path / "test.db")
    key = _cache_key("print('hi')\n")
    store.put(key, _record())
    got = store.get(key)
    store.close()
    assert got["role"] == "角色"
    assert got["key_entities"] == ["A", "b"]   # 存的是 JSON 字符串，取回应还原成列表


def test_get_missing_key_returns_none(tmp_path):
    store = SummaryStore(tmp_path / "test.db")
    assert store.get("no-such-key") is None
    store.close()


def test_cache_key_changes_when_prompt_version_changes(monkeypatch):
    """改提示词版本必须让缓存失效——只 hash 内容的话，调试时会以为改提示词没效果。"""
    monkeypatch.setattr(config, "PROMPT_VERSION", "v1")
    key_v1 = _cache_key("x = 1\n")
    monkeypatch.setattr(config, "PROMPT_VERSION", "v2")
    key_v2 = _cache_key("x = 1\n")
    assert key_v1 != key_v2


def test_cache_key_changes_when_model_changes(monkeypatch):
    monkeypatch.setattr(config, "GLM_MODEL", "glm-4.7-flash")
    key_a = _cache_key("x = 1\n")
    monkeypatch.setattr(config, "GLM_MODEL", "glm-5")
    key_b = _cache_key("x = 1\n")
    assert key_a != key_b


def test_cache_key_changes_when_content_changes():
    assert _cache_key("a = 1\n") != _cache_key("a = 2\n")


def test_from_env_strips_trailing_whitespace(monkeypatch):
    """修过的坑必须锁死：cmd 的 `set X=v2 ` 尾随空格不能进配置值。"""
    monkeypatch.setenv("SOME_FLAG", "v2 ")
    assert config._from_env("SOME_FLAG") == "v2"
    monkeypatch.setenv("OTHER_FLAG", "  spaced  ")
    assert config._from_env("OTHER_FLAG") == "spaced"
