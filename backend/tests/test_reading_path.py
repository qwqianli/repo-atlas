"""这一组在验：阅读顺序的算法核心——方向不能反、环不能误标、结果可复现、文件一个不漏。"""

from analyzer.cli import analyze
from analyzer.reading_path import build_reading_path


def _paths(files):
    return sorted(f["path"] for f in files)


def test_base_module_comes_before_its_dependent(rel_repo):
    """被依赖的先读：pkg/util.py 的序号必须小于依赖它的 pkg/core.py。"""
    a = analyze(str(rel_repo))
    result = build_reading_path(a["files"], a["edges"])
    assert result["order"].index("pkg/util.py") < result["order"].index("pkg/core.py")


def test_order_starts_with_modules_that_import_nothing(rel_repo):
    """方向反没反的第一眼检查：第一名必须是不依赖任何人的叶子模块。"""
    a = analyze(str(rel_repo))
    result = build_reading_path(a["files"], a["edges"])
    assert result["order"][0] in ("pkg/sub/sibling.py", "pkg/util.py")


def test_cycle_fixture_reports_exactly_one_group_and_c_is_not_in_it(cycle_repo):
    """环检测的黄金用例：a↔b 是环，只单向依赖 a 的 c.py 绝不能被误标。"""
    a = analyze(str(cycle_repo))
    result = build_reading_path(a["files"], a["edges"])
    assert result["cycles"] == [["a.py", "b.py"]]
    assert "c.py" not in [p for group in result["cycles"] for p in group]


def test_acyclic_repo_reports_no_cycles(rel_repo):
    a = analyze(str(rel_repo))
    result = build_reading_path(a["files"], a["edges"])
    assert result["cycles"] == []


def test_order_covers_every_file_including_cycle_members(rel_repo, cycle_repo):
    """覆盖性：order 的文件集合必须和 files 完全一致，孤立文件和环成员都不能丢。"""
    for repo in (rel_repo, cycle_repo):
        a = analyze(str(repo))
        result = build_reading_path(a["files"], a["edges"])
        assert set(result["order"]) == set(_paths(a["files"]))
        assert len(result["order"]) == len(result["order"])


def test_result_is_identical_across_three_runs(rel_repo, cycle_repo):
    """可复现性：同一输入连跑三次，输出逐字相同。"""
    for repo in (rel_repo, cycle_repo):
        runs = []
        for _ in range(3):
            a = analyze(str(repo))
            runs.append(build_reading_path(a["files"], a["edges"]))
        assert runs[0] == runs[1] == runs[2]


def test_isolated_file_without_any_import_still_appears(tmp_path):
    """没有任何 import 的文件不进 edges，但必须出现在阅读路线里。"""
    (tmp_path / "lonely.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "user.py").write_text("from lonely import VALUE\n", encoding="utf-8")
    a = analyze(str(tmp_path))
    result = build_reading_path(a["files"], a["edges"])
    assert "lonely.py" in result["order"]
    assert result["order"].index("lonely.py") < result["order"].index("user.py")


def test_cycle_members_are_placed_adjacently(cycle_repo):
    """环组收缩后展开：组内文件在 order 里必须连续出现。"""
    a = analyze(str(cycle_repo))
    result = build_reading_path(a["files"], a["edges"])
    order = result["order"]
    assert abs(order.index("a.py") - order.index("b.py")) == 1
