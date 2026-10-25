"""这一组在验：相对路径 ↔ 模块名的换算规则、文件遍历的目录黑名单、路径统一正斜杠、仓库定位。"""

from pathlib import Path

import pytest

from analyzer.repo_loader import (
    build_module_index,
    clone_or_locate,
    iter_python_files,
    module_of,
)


def test_module_of_plain_file_keeps_all_parts():
    assert module_of(Path("a/b/c.py")) == "a.b.c"


def test_module_of_init_py_represents_its_package():
    assert module_of(Path("a/b/__init__.py")) == "a.b"


def test_module_of_root_init_py_has_no_name():
    """仓库根上的 __init__.py 拿不到模块名（不是任何包的成员）。"""
    assert module_of(Path("__init__.py")) == ""


def test_build_module_index_maps_both_file_forms_to_the_same_key(tmp_path):
    """a/b.py 与 a/b/__init__.py 同时存在时归到同一个键，且包（__init__）获胜。"""
    pkg = tmp_path / "a"
    pkg.mkdir()
    (pkg / "b.py").write_text("", encoding="utf-8")
    (pkg / "b").mkdir()
    (pkg / "b" / "__init__.py").write_text("", encoding="utf-8")
    files = iter_python_files(tmp_path)
    index = build_module_index(tmp_path, files)
    assert index["a.b"] == "a/b/__init__.py"


def test_iter_python_files_skips_blacklisted_directories(tmp_repo):
    for hidden in (".venv", "node_modules", "__pycache__"):
        d = tmp_repo / hidden
        d.mkdir()
        (d / "should_not_appear.py").write_text("x = 1\n", encoding="utf-8")
    found = [f.name for f in iter_python_files(tmp_repo)]
    assert "should_not_appear.py" not in found
    assert sorted(found) == ["base.py", "mid.py", "top.py"]


def test_index_values_always_use_forward_slashes(tmp_path):
    """Windows 反斜杠绝不外泄：索引值统一正斜杠，前端和 SQLite 才不会混。"""
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)
    (nested / "leaf.py").write_text("x = 1\n", encoding="utf-8")
    index = build_module_index(tmp_path, iter_python_files(tmp_path))
    assert index["pkg.sub.leaf"] == "pkg/sub/leaf.py"
    assert "\\" not in index["pkg.sub.leaf"]


def test_clone_or_locate_returns_local_dir_as_is(tmp_repo):
    assert clone_or_locate(str(tmp_repo)) == tmp_repo.resolve()


def test_clone_or_locate_rejects_missing_local_path():
    with pytest.raises(ValueError):
        clone_or_locate("C:/definitely/not/here")


def test_clone_or_locate_reuses_already_cloned_remote(tmp_path):
    """远端仓库本地已克隆过就不再拉取——这是「第二次零成本」的一半承诺。"""
    (tmp_path / "repo").mkdir()
    result = clone_or_locate("https://example.com/group/repo.git", dest_root=tmp_path)
    assert result == (tmp_path / "repo").resolve()


def test_clone_or_locate_reports_clone_failure(tmp_path, monkeypatch):
    from analyzer import repo_loader

    def fake_run(*args, **kwargs):
        class R:
            returncode = 128
            stderr = "fatal: repository not found"
        return R()

    monkeypatch.setattr(repo_loader.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="git clone"):
        clone_or_locate("https://example.com/group/other.git", dest_root=tmp_path)
