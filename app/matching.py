"""二部图匹配核心算法（基于 NetworkX）。

约定
----
- 评审人节点为 ``("reviewer", id)``，稿件节点为 ``("manuscript", id)``，
  从而两侧 ID 即使重名也不会冲突。
- 确定性分配定义为：按评审人 ID 升序逐个决策，序列
  ``(m_{r_1}, m_{r_2}, …)`` 字典序最小；未匹配位置记为 ``null``，
  且 ``null`` 小于任何稿件 ID。
- 每次决策都用「剩余子图的最大匹配基数」做可行性预言，
  预言由 Hopcroft–Karp 完成：``networkx.bipartite.hopcroft_karp_matching``。

Hall 定理证人（409 时）
------------------------
不存在完美匹配时，取确定性最大匹配 M，从未匹配评审人出发沿
M-交替路径（同一交替有向图定向）遍历可达节点 Z：

- Z 中的稿件必然全部已被 M 匹配，否则该路径即增广路，与最大性矛盾；
- 取 S = Z ∩ 评审人，则其在原图中的邻居恰为 Z ∩ 稿件，
  且 |S| - |N(S)| = 未匹配评审人数 = 全局缺口（Kőnig–Ore 定理）。

该 S 即违反 Hall 条件的子集，作为 409 响应中的 ``hall_witness``。

强制性判定与替代分配
--------------------
对完美匹配 M 构造交替有向图 D：

- 匹配边定向为 稿件 -> 评审人；
- 非匹配边定向为 评审人 -> 稿件。

经典结论（Berge）：匹配边 (r, m) 非强制（存在另一个不含它的完美匹配）
当且仅当它落在某个 M-交替环上，当且仅当 r 与 m 在 D 中处于同一个
强连通分量（SCC）。因此：

- 强制边：r 与 m 分属不同 SCC；
- 非强制边：在 D 中 BFS 求 r -> m 的最短路径，与匹配边 (m -> r)
  合成为交替环；环上「匹配边 / 非匹配边」身份翻转后即得到一个
  **完整的**、合法的、不含 (r, m) 的完美匹配，作为替代分配（witness）。

邻接按节点 ID 排序，使 BFS 与全部输出确定性。

最小成本修复（POST /repair）
----------------------------
允许补入带正整数成本的候选配对，求使完美匹配成立的最小总成本方案：

- 允许边权重视为 0、候选边权重视为其成本时，「最小成本完美匹配」所用
  的候选边集合即最小成本补充方案：任一可行补充集 S 都含一个完美匹配，
  该匹配只用到 S 的子集，而正成本下子集成本不超过 S；
- 平局（总成本相同）时，按新增配对 (reviewer_id, manuscript_id) 排序后的
  列表作字典序择优。该比较等价于对「按序是否纳入各候选」的 0/1 指示
  向量取字典序最大：设两边集在第一不同候选 e 处分别为纳入/不纳入，
  正成本保证同成本集合不会互为前缀，故含 e 者的排序列表字典序更小。
  于是第 i 条候选边（按配对排序，共 k 条）赋权
  ``cost * 2^k - 2^(k-1-i)``、允许边赋权 0，一次最小权完美匹配即可
  同时确定两个层级；奖金为 2 的幂，和唯一确定集合，故所有最优解的
  候选边集合相同，补充方案完全确定；
- 最小权完美匹配由 Edmonds 开花算法完成
  （``networkx.max_weight_matching``，纯 Python；权重取负，
  ``maxcardinality=True`` 保证返回完美匹配）；
- 修复成功后的 assignment 在「允许边 + 新增边」图上复用 run_audit，
  沿用字典序最小规则；由正成本可证该分配恰好用完所有新增边
  （否则其候选边子集成本更低，与最小性矛盾）；
- 合并全部候选仍不可行时，在合并图上复用 Hall 证人解释缺口（409）。
"""

from dataclasses import dataclass, field

import networkx as nx

REVIEWER = "reviewer"
MANUSCRIPT = "manuscript"


def _rnode(reviewer_id: str) -> tuple[str, str]:
    return (REVIEWER, reviewer_id)


def _mnode(manuscript_id: str) -> tuple[str, str]:
    return (MANUSCRIPT, manuscript_id)


def _node_key(node: tuple[str, str]) -> tuple[str, str]:
    """邻接排序键：先按侧再按 ID。"""
    return node


def build_graph(
    reviewers: list[str],
    manuscripts: list[str],
    pairings: list[tuple[str, str]],
) -> nx.Graph:
    """由已通过校验的数据构造二部图。"""
    graph = nx.Graph()
    graph.add_nodes_from((_rnode(r) for r in reviewers), side=REVIEWER)
    graph.add_nodes_from((_mnode(m) for m in manuscripts), side=MANUSCRIPT)
    graph.add_edges_from(
        (_rnode(reviewer_id), _mnode(manuscript_id))
        for reviewer_id, manuscript_id in pairings
    )
    return graph


def _reviewer_nodes(graph: nx.Graph) -> list[tuple[str, str]]:
    return [n for n, data in graph.nodes(data=True) if data.get("side") == REVIEWER]


def _residual_view(
    graph: nx.Graph,
    excluded: set[tuple[str, str]],
) -> nx.Graph:
    """剩余子图：排除已定稿评审人节点与已占用稿件节点。"""
    return nx.subgraph_view(graph, filter_node=lambda node: node not in excluded)


def _max_matching_size(graph: nx.Graph) -> int:
    """图中（子图内的）最大匹配基数。"""
    top_nodes = _reviewer_nodes(graph)
    if not top_nodes:
        return 0
    matching = nx.bipartite.hopcroft_karp_matching(graph, top_nodes=top_nodes)
    return len(matching) // 2


@dataclass
class HallWitness:
    """Hall 定理证人：违反 Hall 条件的评审人子集。

    - ``reviewer_ids``：缺口评审人集合 S（已按 ID 排序）；
    - ``neighboring_manuscript_ids``：S 在原允许配对中的**全部**邻居
      （已按 ID 排序）；
    - ``deficiency``：``|S| - |N(S)|``，恒等于全局未匹配评审人数。
    """

    reviewer_ids: list[str]
    neighboring_manuscript_ids: list[str]
    deficiency: int


@dataclass
class AuditResult:
    """算法层审计结果。"""

    perfect_match_exists: bool
    #: 成功时为 (rid, mid, forced, alternative_pairs)；失败时为 (rid, mid)
    pairs: list
    max_matching_size: int
    unmatched_reviewers: list[str] = field(default_factory=list)
    unmatched_manuscripts: list[str] = field(default_factory=list)
    #: 仅 409（不存在完美匹配）时填充。
    hall_witness: HallWitness | None = None


@dataclass
class RepairResult:
    """修复计算结果。

    - ``repairable`` 为 True：``added`` 为按 (rid, mid) 排序的新增配对，
      ``total_cost`` 为最小总成本，``audit`` 为修复后图上的审计结果
      （必然存在完美匹配）；
    - ``repairable`` 为 False：合并全部候选仍不可行，``audit`` 为合并图
      上的审计结果（含 Hall 证人）。
    """

    repairable: bool
    added: list[tuple[str, str]] = field(default_factory=list)
    total_cost: int = 0
    audit: AuditResult | None = None


def lex_smallest_matching(
    graph: nx.Graph,
    reviewers_sorted: list[str],
    target_size: int,
) -> list[tuple[str, str]] | None:
    """求达到指定基数 ``target_size`` 的字典序最小确定性匹配。

    评审人按 ``reviewers_sorted`` 顺序决策；每个评审人优先保持未匹配
    （``null`` 字典序最小），否则在仍能完成目标基数的前提下选择最小稿件。
    无法达到目标基数时返回 ``None``。
    """
    decided: set[tuple[str, str]] = set()
    used_manuscripts: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    remaining = target_size

    for reviewer_id in reviewers_sorted:
        rnode = _rnode(reviewer_id)

        # 1) 尝试让该评审人未匹配（null）：仅当剩余评审人仍能凑满基数。
        #    必须同时排除所有已定稿评审人（其边固定，不可重算）
        #    与已占用稿件（不可再分配），否则基数预言会虚高。
        view = _residual_view(graph, decided | {rnode} | used_manuscripts)
        if _max_matching_size(view) >= remaining:
            decided.add(rnode)
            continue

        # 2) 必须匹配：按稿件 ID 升序枚举，选第一个仍可完成目标基数的候选。
        chosen: str | None = None
        candidates = sorted(
            manuscript_id for _, manuscript_id in graph.adj[rnode]
        )
        for manuscript_id in candidates:
            mnode = _mnode(manuscript_id)
            if mnode in used_manuscripts:
                continue
            view = _residual_view(
                graph, decided | {rnode, mnode} | used_manuscripts
            )
            if _max_matching_size(view) >= remaining - 1:
                chosen = manuscript_id
                break

        decided.add(rnode)
        if chosen is None:
            # 理论上不会发生（目标基数可达），保险处理使整轮失败。
            continue

        used_manuscripts.add(_mnode(chosen))
        result.append((reviewer_id, chosen))
        remaining -= 1

    if remaining != 0:
        return None
    return result


def _build_alternating_digraph(
    graph: nx.Graph,
    matching: list[tuple[str, str]],
) -> nx.DiGraph:
    """由匹配 matching（不必完美）构造交替有向图 D。

    匹配边定向 稿件 -> 评审人；非匹配边定向 评审人 -> 稿件。
    """
    digraph = nx.DiGraph()
    digraph.add_nodes_from(graph.nodes())
    matched_edges = {
        (_rnode(reviewer_id), _mnode(manuscript_id))
        for reviewer_id, manuscript_id in matching
    }
    for u, v in graph.edges():
        rnode = u if u[0] == REVIEWER else v
        mnode = u if u[0] == MANUSCRIPT else v
        if (rnode, mnode) in matched_edges:
            digraph.add_edge(mnode, rnode)  # 匹配边
        else:
            digraph.add_edge(rnode, mnode)  # 非匹配边
    return digraph


def _sorted_successors(digraph: nx.DiGraph, node: tuple[str, str]):
    return sorted(digraph.successors(node), key=_node_key)


def _cycle_alternative(
    digraph: nx.DiGraph,
    canonical: list[tuple[str, str]],
    reviewer_id: str,
    manuscript_id: str,
) -> list[tuple[str, str]]:
    """沿含匹配边 (r, m) 的最短交替环翻转，构造不含该边的完美匹配。

    BFS 求 D 中 r -> m 的最短有向路径（邻接有序 => 结果确定），
    路径边全部为非匹配边方向（r→m），再补上匹配边 (m→r) 成环；
    环上身份翻转即得替代完美匹配。
    """
    rnode = _rnode(reviewer_id)
    mnode = _mnode(manuscript_id)

    # BFS：r -> ... -> m。
    parents: dict[tuple[str, str], tuple[str, str] | None] = {rnode: None}
    queue = [rnode]
    found = False
    head = 0
    while head < len(queue):
        current = queue[head]
        head += 1
        if current == mnode:
            found = True
            break
        for nxt in _sorted_successors(digraph, current):
            if nxt not in parents:
                parents[nxt] = current
                queue.append(nxt)
    if not found:
        # 与 SCC 结论矛盾：非强制边必有环。
        raise RuntimeError(f"未找到含 ({reviewer_id}, {manuscript_id}) 的交替环")

    # 还原路径 r -> ... -> m；路径在二部图上交替经过 评审人/稿件。
    path: list[tuple[str, str]] = []
    current = mnode
    while current is not None:
        path.append(current)
        current = parents[current]
    path.reverse()

    # 路径上的无向边（全部是非匹配边）变为匹配边。
    flipped_non_matched: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    for a, b in zip(path, path[1:]):
        flipped_non_matched.add((a, b))
        flipped_non_matched.add((b, a))

    # 以原完美匹配为基底，在环上翻转：移除路径对应的匹配边（m -> r 配对），
    # 加入路径上的非匹配边（r -> m 配对）。
    # 路径节点（不含起点 r）依次为 m?, r?, m?, ... 成对：
    # path = [r0, m_a, r_a, m_b, r_b, ..., m_target]
    alternative: list[tuple[str, str]] = []
    on_path = set(path)
    for rid, mid in canonical:
        rn = _rnode(rid)
        mn = _mnode(mid)
        if rn in on_path and mn in on_path:
            # 环上原匹配边（包括被审计边本身）：丢弃，由非匹配边取代。
            continue
        alternative.append((rid, mid))
    for a, b in zip(path, path[1:]):
        if a[0] == REVIEWER and b[0] == MANUSCRIPT:
            alternative.append((a[1], b[1]))

    alternative.sort(key=lambda pair: pair[0])
    return alternative


def hall_witness(
    graph: nx.Graph,
    matching: list[tuple[str, str]],
    unmatched_reviewers: list[str],
) -> HallWitness:
    """由最大匹配出发，沿交替路径构造 Hall 定理证人。

    以未匹配评审人为源，在交替有向图（匹配边 稿件→评审人，非匹配边
    评审人→稿件）中遍历可达节点集 Z：

    - Z 中的稿件全部已被匹配，否则该交替路径即增广路，与最大性矛盾；
    - 取 S = Z ∩ 评审人：S 中已匹配者的匹配边沿路径反向可达，未匹配者
      的任意邻居沿非匹配边可达，故 S 在原图中的邻居恰为 Z ∩ 稿件；
    - Z ∩ 稿件与 S 中已匹配者一一配对，故
      ``|S| - |N(S)|`` = 未匹配评审人数 = 全局缺口。

    源点与邻接均按 ID 排序，且可达集与遍历顺序无关，结果完全确定。
    """
    digraph = _build_alternating_digraph(graph, matching)
    sources = sorted(
        (_rnode(reviewer_id) for reviewer_id in unmatched_reviewers),
        key=_node_key,
    )
    reachable: set[tuple[str, str]] = set(sources)
    queue = list(sources)
    head = 0
    while head < len(queue):
        current = queue[head]
        head += 1
        for nxt in _sorted_successors(digraph, current):
            if nxt not in reachable:
                reachable.add(nxt)
                queue.append(nxt)

    witness_reviewers = sorted(
        node_id for side, node_id in reachable if side == REVIEWER
    )
    witness_manuscripts = sorted(
        node_id for side, node_id in reachable if side == MANUSCRIPT
    )
    deficiency = len(witness_reviewers) - len(witness_manuscripts)
    # 交替路径理论保证缺口恰为全局未匹配人数；防御性断言。
    assert deficiency == len(unmatched_reviewers)
    return HallWitness(
        reviewer_ids=witness_reviewers,
        neighboring_manuscript_ids=witness_manuscripts,
        deficiency=deficiency,
    )


def run_audit(
    reviewers: list[str],
    manuscripts: list[str],
    pairings: list[tuple[str, str]],
) -> AuditResult:
    """执行完整审计：最大匹配 / 确定性完美匹配 / 逐对强制性。"""
    graph = build_graph(reviewers, manuscripts, pairings)
    reviewers_sorted = sorted(reviewers)
    max_size = _max_matching_size(graph)

    # 不存在完美匹配：给出确定性的最大匹配、未匹配双方及 Hall 定理证人。
    if max_size != len(reviewers):
        pairs = lex_smallest_matching(graph, reviewers_sorted, max_size)
        # 目标基数由全局最大匹配保证可行；防御性断言。
        assert pairs is not None
        matched_reviewers = {r for r, _ in pairs}
        matched_manuscripts = {m for _, m in pairs}
        unmatched_reviewers = sorted(set(reviewers) - matched_reviewers)
        return AuditResult(
            perfect_match_exists=False,
            pairs=pairs,
            max_matching_size=max_size,
            unmatched_reviewers=unmatched_reviewers,
            unmatched_manuscripts=sorted(set(manuscripts) - matched_manuscripts),
            hall_witness=hall_witness(graph, pairs, unmatched_reviewers),
        )

    # 存在完美匹配：取字典序最小的确定性分配。
    canonical = lex_smallest_matching(graph, reviewers_sorted, len(reviewers))
    assert canonical is not None

    # 交替有向图 + 强连通分量：一次 O(V+E) 得到全部强制边。
    digraph = _build_alternating_digraph(graph, canonical)
    component: dict[tuple[str, str], int] = {}
    for index, members in enumerate(nx.strongly_connected_components(digraph)):
        for node in members:
            component[node] = index

    entries: list[tuple[str, str, bool, list[tuple[str, str]] | None]] = []
    for reviewer_id, manuscript_id in canonical:
        same_scc = (
            component[_rnode(reviewer_id)] == component[_mnode(manuscript_id)]
        )
        if not same_scc:
            # 两端不同 SCC：该边不在任何交替环上，为强制边。
            entries.append((reviewer_id, manuscript_id, True, None))
            continue
        # 同一 SCC：非强制，给出环翻转得到的完整替代分配。
        alternative = _cycle_alternative(
            digraph, canonical, reviewer_id, manuscript_id
        )
        entries.append((reviewer_id, manuscript_id, False, alternative))

    return AuditResult(
        perfect_match_exists=True,
        pairs=entries,
        max_matching_size=max_size,
    )


def _min_cost_added_set(
    graph: nx.Graph,
    reviewers: list[str],
    candidates: list[tuple[str, str, int]],
) -> list[tuple[str, str]]:
    """在含全部候选边的图上求最小成本完美匹配，返回其候选边集合（已排序）。

    第 i 条候选边（按 (rid, mid) 排序，共 k 条）赋权
    ``cost * 2^k - 2^(k-1-i)``，允许边权 0：主目标最小化总成本，
    次目标最大化按序纳入指示向量的字典序（等价于新增配对排序列表
    字典序最小）。奖金为 2 的幂，其和唯一确定集合，故所有最优解的
    候选边集合相同，返回值与开花算法的内部平局无关。
    """
    ordered = sorted(candidates, key=lambda candidate: (candidate[0], candidate[1]))
    k = len(ordered)
    scale = 1 << k

    weighted = nx.Graph(graph)
    for u, v, data in weighted.edges(data=True):
        data["weight"] = 0
    for index, (reviewer_id, manuscript_id, cost) in enumerate(ordered):
        bonus = 1 << (k - 1 - index)
        # max_weight_matching 求最大权：取负即最小化上述扰动权重。
        weighted[_rnode(reviewer_id)][_mnode(manuscript_id)]["weight"] = (
            bonus - cost * scale
        )

    matching = nx.max_weight_matching(
        weighted, maxcardinality=True, weight="weight"
    )
    # 完美匹配的存在性已前置校验；防御性断言。
    assert len(matching) == len(reviewers)

    candidate_ids = {(r, m) for r, m, _ in candidates}
    added: list[tuple[str, str]] = []
    for a, b in matching:
        reviewer_id, manuscript_id = (
            (a[1], b[1]) if a[0] == REVIEWER else (b[1], a[1])
        )
        if (reviewer_id, manuscript_id) in candidate_ids:
            added.append((reviewer_id, manuscript_id))
    added.sort()
    return added


def run_repair(
    reviewers: list[str],
    manuscripts: list[str],
    pairings: list[tuple[str, str]],
    candidates: list[tuple[str, str, int]],
) -> RepairResult:
    """最小成本修复：补入候选配对使完美匹配成立。

    ``candidates`` 为已通过校验的 (rid, mid, cost) 列表（与允许配对不
    重叠、互不重复、成本为正整数）。
    """
    base_graph = build_graph(reviewers, manuscripts, pairings)
    if _max_matching_size(base_graph) == len(reviewers):
        # 原图已可行：零成本空补充，直接审计原图。
        return RepairResult(
            repairable=True,
            audit=run_audit(reviewers, manuscripts, pairings),
        )

    merged_pairings = list(pairings) + [(r, m) for r, m, _ in candidates]
    merged_graph = build_graph(reviewers, manuscripts, merged_pairings)
    if _max_matching_size(merged_graph) != len(reviewers):
        # 合并全部候选仍不可行：复用合并图上的 Hall 证人解释缺口。
        return RepairResult(
            repairable=False,
            audit=run_audit(reviewers, manuscripts, merged_pairings),
        )

    added = _min_cost_added_set(merged_graph, reviewers, candidates)
    cost_of = {(r, m): cost for r, m, cost in candidates}
    total_cost = sum(cost_of[edge] for edge in added)
    return RepairResult(
        repairable=True,
        added=added,
        total_cost=total_cost,
        audit=run_audit(reviewers, manuscripts, list(pairings) + added),
    )
