"""这一组在验：import 语句的解析与归类（标准库 / 第三方 / 项目内），以及坏文件不会让流程崩掉。"""

from analyzer.ast_parser import (
    RawImport,
    build_graph,
    extract_imports,
    resolve_raw_imports,
)


def _resolve(raw, current_module, index):
    """一行走完归类，返回 (internal, external, stdlib) 三个列表。"""
    return resolve_raw_imports(raw, current_module, index)


def test_import_os_is_classified_as_stdlib():
    internal, external, stdlib = _resolve(
        [RawImport(module="os", names=[], level=0)], "app", {}
    )
    assert stdlib == ["os"]
    assert internal == [] and external == []


def test_import_requests_is_classified_as_external():
    _, external, _ = _resolve(
        [RawImport(module="requests", names=[], level=0)], "app", {}
    )
    assert external == ["requests"]


def test_import_config_is_classified_as_internal_when_module_index_has_it():
    internal, external, _ = _resolve(
        [RawImport(module="config", names=[], level=0)], "app", {"config": "config.py"}
    )
    assert internal == ["config"] and external == []


def test_external_only_keeps_top_level_package_name():
    """import playwright.sync_api 只记 playwright，不记全路径。"""
    _, external, _ = _resolve(
        [RawImport(module="playwright.sync_api", names=[], level=0)], "app", {}
    )
    assert external == ["playwright"]


def test_from_import_records_module_and_submodule_when_both_exist():
    """from a.b import c 且 a.b.c 确实是模块时，a.b 和 a.b.c 两条内部依赖都要记。"""
    index = {"a.b": "a/b.py", "a.b.c": "a/b/c.py"}
    internal, _, _ = _resolve(
        [RawImport(module="a.b", names=["c"], level=0)], "top", index
    )
    assert internal == ["a.b", "a.b.c"]


def test_from_import_skips_submodule_when_it_is_just_a_name():
    index = {"a.b": "a/b.py"}
    internal, _, _ = _resolve(
        [RawImport(module="a.b", names=["some_function"], level=0)], "top", index
    )
    assert internal == ["a.b"]


def test_init_py_and_plain_module_resolve_to_the_same_key():
    """a/b.py 和 a/b/__init__.py 都归到 a.b：查表用哪条路径形式的索引都能命中。"""
    for value in ("a/b.py", "a/b/__init__.py"):
        internal, _, _ = _resolve(
            [RawImport(module="a.b", names=[], level=0)], "top", {"a.b": value}
        )
        assert internal == ["a.b"], f"索引值 {value} 应命中同一个键"


def test_level1_relative_import_resolves_against_parent_package():
    """pkg/core.py 里的 from . import util → pkg.util（不是 util，也不是 pkg 自己）。"""
    index = {"pkg": "pkg/__init__.py", "pkg.util": "pkg/util.py"}
    internal, _, _ = _resolve(
        [RawImport(module="", names=["util"], level=1)], "pkg.core", index
    )
    assert internal == ["pkg.util"]


def test_level2_relative_import_walks_up_two_levels():
    """pkg/core.py 里的 from ..util import y → 顶层 util。"""
    index = {"util": "util.py"}
    internal, _, _ = _resolve(
        [RawImport(module="util", names=["y"], level=2)], "pkg.core", index
    )
    assert internal == ["util"]


def test_level4_relative_import_walks_up_four_levels():
    index = {"a.base": "a/base.py"}
    internal, _, _ = _resolve(
        [RawImport(module="base", names=["z"], level=4)], "a.b.c.d.mod", index
    )
    assert internal == ["a.base"]


def test_from_dot_import_never_adds_own_package_as_dependency():
    """修过的假环 bug 必须锁死：from . import x 不得把当前文件所在的包记成依赖。"""
    index = {"pkg": "pkg/__init__.py", "pkg.util": "pkg/util.py"}
    internal, _, _ = _resolve(
        [RawImport(module="", names=["util"], level=1)], "pkg.core", index
    )
    assert "pkg" not in internal
    assert internal == ["pkg.util"]


def test_relative_import_in_init_py_takes_itself_as_base():
    """包的 __init__.py 里 from . import x：'.' 指包自己，解析结果应是 pkg.x。"""
    index = {"pkg": "pkg/__init__.py", "pkg.x": "pkg/x.py"}
    internal, _, _ = _resolve(
        [RawImport(module="", names=["x"], level=1)], "pkg", index
    )
    assert internal == ["pkg.x"]


def test_syntax_error_file_returns_reason_instead_of_raising(tmp_path):
    bad = tmp_path / "broken.py"
    bad.write_text("def f(:\n", encoding="utf-8")
    imports, error = extract_imports(bad)
    assert imports == []
    assert error.startswith("SyntaxError")


def test_nul_byte_file_returns_reason_instead_of_raising(tmp_path):
    bad = tmp_path / "nul.py"
    bad.write_bytes(b"import os\n\x00garbage\n")
    imports, error = extract_imports(bad)
    assert imports == []
    # Python 3.13 对 NUL 字节抛 SyntaxError（旧版是 ValueError），两种兜底都接得住
    assert "null bytes" in error


def test_non_utf8_file_returns_reason_instead_of_raising(tmp_path):
    bad = tmp_path / "gbk.py"
    bad.write_bytes(b"# \xd6\xd0\xce\xc4\xb2\xbb\xd5\xfd\xc8\xb7\nx = 1\n")
    imports, error = extract_imports(bad)
    assert imports == []
    assert "UnicodeDecodeError" in error


def test_import_inside_function_is_still_extracted(tmp_path):
    f = tmp_path / "lazy.py"
    f.write_text("def run():\n    import json\n", encoding="utf-8")
    imports, error = extract_imports(f)
    assert error is None
    assert imports == [RawImport(module="json", names=[], level=0)]


def test_self_loop_edge_is_removed_but_cycle_edges_are_kept():
    """自环（A→A）要去掉；A→B→A 的环必须原样保留，拓扑排序靠它找环。"""
    analysis = {
        "files": [
            {"path": "a.py", "module": "a", "internal": ["a", "b"]},
            {"path": "b.py", "module": "b", "internal": ["a"]},
        ],
        "edges": [],
    }
    result = build_graph(analysis)
    assert {"source": "a.py", "target": "a.py"} not in result["edges"]
    assert {"source": "a.py", "target": "b.py"} in result["edges"]
    assert {"source": "b.py", "target": "a.py"} in result["edges"]
