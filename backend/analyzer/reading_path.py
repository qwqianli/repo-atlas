"""从依赖边计算「建议阅读顺序」：底层模块最先读，循环依赖单独成组。

纯函数模块：不读文件、不碰 Flask、不打印——P4 可以直接对它写单元测试。

为什么这样做：
- 边的含义是 source import target，被 import 的 target 更"底层"、应该先读。
  所以对【反向优先图】跑 Kahn：入度为 0 = 不 import 任何项目内模块的叶子。
- 环必须用强连通分量（Tarjan）找，不能把"Kahn 剩下的节点"全算环——
  那会把只是单向依赖了环的普通文件（cycle fixture 里的 c.py）误标成循环依赖。
- 环组收缩成超节点再排序，环内文件连续排在一起；所有"同时可选"的地方
  一律按路径字典序取，保证同一输入永远得到同一输出。
"""

import heapq


def build_reading_path(files: list[dict], edges: list[dict]) -> dict:
    """从依赖边算出阅读顺序。files / edges 是 P0 analysis 里的两个字段。"""
    # 节点全集取自 files 而不是 edges：没有任何依赖边的孤立文件也必须出现在路线里
    paths = sorted(f["path"] for f in files)
    imports: dict[str, list[str]] = {p: [] for p in paths}
    self_loops: set[str] = set()
    for edge in edges:
        source, target = edge["source"], edge["target"]
        if source == target:
            self_loops.add(source)   # P0 正常输出已去自环，纯函数这里兜底
            continue
        imports[source].append(target)

    components = _tarjan_scc(paths, imports)

    # 每个强连通分量收缩成一个"超节点"；大小 > 1（或自环）的就是循环依赖组
    groups: list[list[str]] = []
    cycle_groups: list[list[str]] = []
    group_of: dict[str, int] = {}
    for component in components:
        members = sorted(component)   # 组内按路径字典序：同一个环每次展开顺序都一致
        gid = len(groups)
        groups.append(members)
        if len(members) > 1 or members[0] in self_loops:
            cycle_groups.append(members)
        for path in members:
            group_of[path] = gid

    # 反向优先图：原图边 s→t（s import t）翻译成"t 的组要先于 s 的组"。
    # 于是某组的"入度" = 它 import 的外部组个数，入度 0 就是可以立刻读的底层。
    indegree = [0] * len(groups)
    read_after: list[set[int]] = [set() for _ in groups]   # 组 g 读完，哪些组可能解锁
    for source, targets in imports.items():
        for target in targets:
            reader, base = group_of[source], group_of[target]
            if reader == base:
                continue   # 环内互指，收缩掉
            if reader not in read_after[base]:
                read_after[base].add(reader)
                indegree[reader] += 1

    # Kahn。堆里放 (组内最小路径, 组号)：同时可选时永远先弹路径最小的组，结果可复现
    ready = [(groups[g][0], g) for g in range(len(groups)) if indegree[g] == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        _, gid = heapq.heappop(ready)
        order.extend(groups[gid])
        for nxt in read_after[gid]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(ready, (groups[nxt][0], nxt))

    # Tarjan 已把环全部收缩，Kahn 一定能排完所有组；对不上说明实现有 bug，宁可炸也不给错结果
    if len(order) != len(paths):
        raise RuntimeError(f"阅读路线漏文件: 排出 {len(order)} 个，实际 {len(paths)} 个")

    cycle_groups.sort()   # 组间也按字典序，输出稳定
    return {
        "order": order,
        "cycles": cycle_groups,
        "reason": "反向拓扑序：不依赖项目内模块的底层文件最先读；循环依赖组连续放置，组内按路径字典序",
    }


def _tarjan_scc(nodes: list[str], imports: dict[str, list[str]]) -> list[list[str]]:
    """迭代版 Tarjan 强连通分量。

    用显式栈模拟递归：几百个文件的深依赖链也不会触发递归深度上限。
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0

    for root in nodes:
        if root in index:
            continue
        # 调用栈每帧是 (节点, 下一个要看的邻居下标)；i==0 表示这是第一次进入该节点
        calls: list[tuple[str, int]] = [(root, 0)]
        while calls:
            node, i = calls.pop()
            if i == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            descended = False
            neighbors = imports[node]
            while i < len(neighbors):
                n = neighbors[i]
                i += 1
                if n not in index:
                    # 存回本帧进度，再"递归"进邻居；回来时从第 i 个邻居继续
                    calls.append((node, i))
                    calls.append((n, 0))
                    descended = True
                    break
                if n in on_stack:
                    low[node] = min(low[node], index[n])
            if descended:
                continue
            if low[node] == index[node]:
                component = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    component.append(w)
                    if w == node:
                        break
                components.append(component)
            if calls:
                parent = calls[-1][0]
                low[parent] = min(low[parent], low[node])
    return components
