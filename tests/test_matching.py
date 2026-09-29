"""核心算法测试：以全排列暴力枚举交叉验证字典序、强制性与最大匹配。"""

import itertools

import pytest

from app.matching import run_audit


def all_perfect_matchings(reviewers, manuscripts, edges):
    """暴力枚举所有完美匹配，返回按评审人排序的 (rid -> mid) 元组集合。"""
    adj = {r: [] for r in reviewers}
    for r, m in edges:
        adj[r].append(m)
    results = set()
    for perm in itertools.permutations(sorted(manuscripts)):
        mapping = dict(zip(sorted(reviewers), perm))
        if all(mapping[r] in adj[r] for r in sorted(reviewers)):
            results.add(tuple(mapping[r] for r in sorted(reviewers)))
    return results


def all_matchings(reviewers, manuscripts, edges, size):
    """暴力枚举指定基数的全部匹配（用 reviewer 子集 × 分配）。"""
    adj = {r: [m for m in manuscripts if (r, m) in edges] for r in reviewers}
    out = []
    for k in range(size, len(reviewers) + 1):
        for subset in itertools.combinations(sorted(reviewers), k):
            for chosen in itertools.permutations(sorted(manuscripts), k):
                if all(chosen[i] in adj[r] for i, r in enumerate(subset)):
                    out.append((subset, chosen))
    return out


def lex_key(seq):
    """未匹配记 None，None 小于任何字符串（用标签元组避免 None/str 比较）。"""
    return tuple((0, "") if m is None else (1, m) for m in seq)


def assert_hall_witness(result, reviewers, manuscripts, edges):
    """Hall 证人通用不变量（对任意无完美匹配的输入成立）。"""
    witness = result.hall_witness
    assert witness is not None
    # 列表按 ID 排序且证人非空。
    assert witness.reviewer_ids == sorted(witness.reviewer_ids)
    assert witness.neighboring_manuscript_ids == sorted(
        witness.neighboring_manuscript_ids
    )
    assert witness.reviewer_ids
    assert set(witness.reviewer_ids) <= set(reviewers)
    assert set(witness.neighboring_manuscript_ids) <= set(manuscripts)
    # 邻居集合必须**恰为**证人评审人在原 allowed_pairings 中的全部邻居。
    edge_set = set(edges)
    true_neighbors = {
        m
        for r in witness.reviewer_ids
        for m in manuscripts
        if (r, m) in edge_set
    }
    assert set(witness.neighboring_manuscript_ids) == true_neighbors
    # deficiency = 评审人数 - 邻居数 = 全局未匹配评审人数。
    assert witness.deficiency == len(witness.reviewer_ids) - len(
        witness.neighboring_manuscript_ids
    )
    assert witness.deficiency == len(reviewers) - result.max_matching_size
    assert witness.deficiency == len(result.unmatched_reviewers)
    assert witness.deficiency >= 1


@pytest.mark.parametrize("seed", range(200))
def test_audit_matches_bruteforce(seed):
    import random

    rng = random.Random(seed)
    n = rng.randint(1, 4)
    reviewers = [f"r{i}" for i in range(n)]
    manuscripts = [f"m{i}" for i in range(n)]
    all_edges = [(r, m) for r in reviewers for m in manuscripts]
    k = rng.randint(1, len(all_edges))
    edges = rng.sample(all_edges, k)

    result = run_audit(reviewers, manuscripts, edges)
    perfects = all_perfect_matchings(reviewers, manuscripts, edges)

    if not perfects:
        assert result.perfect_match_exists is False
        assert len(result.pairs) == result.max_matching_size
        # 未匹配数量两侧相等且互补。
        assert len(result.unmatched_reviewers) == n - result.max_matching_size
        assert len(result.unmatched_manuscripts) == n - result.max_matching_size
        matched_r = {r for r, _ in result.pairs}
        matched_m = {m for _, m in result.pairs}
        assert result.unmatched_reviewers == sorted(set(reviewers) - matched_r)
        assert result.unmatched_manuscripts == sorted(set(manuscripts) - matched_m)
        # 不存在更大匹配（暴力：该基数下没有更大基数的匹配）。
        assert not all_matchings(
            reviewers, manuscripts, edges, result.max_matching_size + 1
        )
        # 确定性匹配本身合法。
        assert all((r, m) in edges for r, m in result.pairs)
        # 确定性：与字典序最小的 null-aware 最大匹配一致。
        canon = lex_smallest_bruteforce(reviewers, manuscripts, edges, perfects)
        assert sorted(result.pairs) == sorted(canon)
        assert_hall_witness(result, reviewers, manuscripts, edges)
        return

    assert result.perfect_match_exists is True
    assert result.max_matching_size == n

    sorted_r = sorted(reviewers)
    canonical = min(perfects, key=lex_key)
    got = {r: (m, forced, alt) for r, m, forced, alt in result.pairs}

    for index, reviewer in enumerate(sorted_r):
        manuscript, forced, alternative = got[reviewer]
        assert manuscript == canonical[index]

        # 强制：每个完美匹配都含此边。
        truly_forced = all(p[index] == manuscript for p in perfects)
        assert forced is truly_forced

        if not forced:
            assert alternative is not None
            alt_map = dict(alternative)
            assert len(alternative) == n
            # 替代分配完整、合法、且不含被审计边。
            assert alt_map[reviewer] != manuscript
            alt_tuple = tuple(alt_map[r] for r in sorted_r)
            assert alt_tuple in perfects

    # 排序：输出按评审人 ID 升序。
    assert [r for r, *_ in result.pairs] == sorted_r


def lex_smallest_bruteforce(reviewers, manuscripts, edges, perfects):
    """暴力求字典序最小（None 最小）的最大基数匹配，作为 409 对照。"""
    if perfects:
        return list(zip(sorted(reviewers), min(perfects)))

    adj = {r: {m for m in manuscripts if (r, m) in edges} for r in reviewers}
    m_sorted = sorted(manuscripts)
    r_sorted = sorted(reviewers)

    def gen(i, used, chosen):
        if i == len(r_sorted):
            yield tuple(chosen)
            return
        yield from gen(i + 1, used, chosen + [None])
        for m in m_sorted:
            if m not in used and m in adj[r_sorted[i]]:
                yield from gen(i + 1, used | {m}, chosen + [m])

    candidates = list(gen(0, set(), []))
    max_card = max(sum(m is not None for m in seq) for seq in candidates)
    best_seq = min(
        (seq for seq in candidates if sum(m is not None for m in seq) == max_card),
        key=lex_key,
    )
    return [(r, m) for r, m in zip(r_sorted, best_seq) if m is not None]


def test_known_forced_edges():
    """经典例：r2/m2 为强制边；r0、r1 可互换。"""
    reviewers = ["r0", "r1", "r2"]
    manuscripts = ["m0", "m1", "m2"]
    edges = [("r0", "m0"), ("r0", "m1"), ("r1", "m0"), ("r1", "m1"), ("r2", "m2")]
    result = run_audit(reviewers, manuscripts, edges)
    assert result.perfect_match_exists is True
    by_r = {r: (m, forced) for r, m, forced, _ in result.pairs}
    assert by_r["r0"] == ("m0", False)
    assert by_r["r1"] == ("m1", False)
    assert by_r["r2"] == ("m2", True)


def test_hall_witness_multi_deficiency():
    """缺口大于 1：四名评审人共享唯一邻居 m0，r4 自给自足。

    最大匹配基数为 2（m0 与 m1 各匹配一人），全局缺口为 3，
    证人必须一次暴露全部 3 人缺口而非逐一枚举。
    """
    reviewers = ["r0", "r1", "r2", "r3", "r4"]
    manuscripts = ["m0", "m1", "m2", "m3", "m4"]
    edges = [(f"r{i}", "m0") for i in range(4)] + [
        ("r4", "m1"),
        ("r4", "m2"),
        ("r4", "m3"),
        ("r4", "m4"),
    ]
    result = run_audit(reviewers, manuscripts, edges)
    assert result.perfect_match_exists is False
    assert result.max_matching_size == 2
    assert len(result.unmatched_reviewers) == 3
    witness = result.hall_witness
    assert witness.reviewer_ids == ["r0", "r1", "r2", "r3"]
    assert witness.neighboring_manuscript_ids == ["m0"]
    assert witness.deficiency == 3


def test_hall_witness_includes_matched_reviewers():
    """证人可包含已匹配评审人：r2 被匹配，但仍在缺口子集中。

    r0/r1/r2 共同竞争唯一稿件 m0；字典序最小匹配把 m0 分给 r2，
    未匹配者为 {r0, r1}。若只取未匹配者，邻居仍为 {m0}，缺口只有 1，
    小于全局缺口 2 —— 必须沿交替路径把 r2 一并纳入。
    """
    reviewers = ["r0", "r1", "r2", "r3"]
    manuscripts = ["m0", "m1", "m2", "m3"]
    edges = [("r0", "m0"), ("r1", "m0"), ("r2", "m0"), ("r3", "m3")]
    result = run_audit(reviewers, manuscripts, edges)
    assert result.perfect_match_exists is False
    assert result.max_matching_size == 2
    assert result.unmatched_reviewers == ["r0", "r1"]
    witness = result.hall_witness
    assert witness.reviewer_ids == ["r0", "r1", "r2"]
    assert witness.neighboring_manuscript_ids == ["m0"]
    assert witness.deficiency == 2
    # 证人真包含未匹配集合（含已匹配的 r2），且为全体评审人的真子集。
    assert set(result.unmatched_reviewers) < set(witness.reviewer_ids)
    assert set(witness.reviewer_ids) < set(reviewers)


def test_hall_witness_non_obvious_subset():
    """非显然 Hall 子集：未匹配者自身的邻居并不构成缺口。

    r0 未匹配，但其邻居 {m0, m1} 多达两个，单独看不违反 Hall 条件；
    沿交替路径可达 r1（经 m0）与 r2（经 m1），三者合起来只有
    {m0, m1} 两个邻居，缺口 1 恰为全局缺口。
    """
    reviewers = ["r0", "r1", "r2"]
    manuscripts = ["m0", "m1", "m2"]
    edges = [("r0", "m0"), ("r0", "m1"), ("r1", "m0"), ("r2", "m1")]
    result = run_audit(reviewers, manuscripts, edges)
    assert result.perfect_match_exists is False
    assert result.max_matching_size == 2
    assert result.unmatched_reviewers == ["r0"]
    witness = result.hall_witness
    assert witness.reviewer_ids == ["r0", "r1", "r2"]
    assert witness.neighboring_manuscript_ids == ["m0", "m1"]
    assert witness.deficiency == 1


def test_hall_witness_empty_neighborhood():
    """极端情形：未匹配评审人没有任何允许配对，邻居集为空。"""
    reviewers = ["r0", "r1", "r2"]
    manuscripts = ["m0", "m1", "m2"]
    edges = [("r0", "m0"), ("r1", "m1")]
    result = run_audit(reviewers, manuscripts, edges)
    assert result.perfect_match_exists is False
    witness = result.hall_witness
    assert witness.reviewer_ids == ["r2"]
    assert witness.neighboring_manuscript_ids == []
    assert witness.deficiency == 1
