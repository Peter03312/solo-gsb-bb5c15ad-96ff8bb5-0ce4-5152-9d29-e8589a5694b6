"""POST /repair：最小成本修复的 API 测试与暴力枚举交叉验证。"""

import itertools
import random

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.matching import run_repair

client = TestClient(app)


def payload(**overrides):
    """基础输入：r2 无任何允许配对，需候选边修复。"""
    base = {
        "reviewers": ["r0", "r1", "r2"],
        "manuscripts": ["m0", "m1", "m2"],
        "allowed_pairings": [
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m1"},
        ],
        "candidate_pairings": [
            {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 5},
        ],
    }
    base.update(overrides)
    return base


def audit_view(body):
    """同一输入去掉候选字段后的 /audit 请求体。"""
    return {k: v for k, v in body.items() if k != "candidate_pairings"}


# ---------------------------------------------------------------- 200


def test_repair_success_single_edge():
    resp = client.post("/repair", json=payload())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["perfect_match_exists"] is True
    assert data["added_pairings"] == [
        {"reviewer_id": "r2", "manuscript_id": "m2"}
    ]
    assert data["total_cost"] == 5
    assignment = data["assignment"]
    assert [(a["reviewer_id"], a["manuscript_id"]) for a in assignment] == [
        ("r0", "m0"),
        ("r1", "m1"),
        ("r2", "m2"),
    ]
    # 修复后图只有唯一完美匹配：全部强制（沿用 /audit 的标注结构）。
    assert all(a["forced"] is True for a in assignment)
    assert all(a["alternative"] is None for a in assignment)


def test_repair_prefers_cheaper_subset():
    # 一条贵边直达 vs 两条便宜边绕行：选总成本更小者。
    body = payload(
        candidate_pairings=[
            {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 9},
            {"reviewer_id": "r2", "manuscript_id": "m0", "cost": 2},
            {"reviewer_id": "r0", "manuscript_id": "m2", "cost": 2},
        ],
    )
    resp = client.post("/repair", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["added_pairings"] == [
        {"reviewer_id": "r0", "manuscript_id": "m2"},
        {"reviewer_id": "r2", "manuscript_id": "m0"},
    ]
    assert data["total_cost"] == 4
    assert [(a["reviewer_id"], a["manuscript_id"]) for a in data["assignment"]] == [
        ("r0", "m2"),
        ("r1", "m1"),
        ("r2", "m0"),
    ]


def test_repair_tie_break_lexicographic():
    # 两种修复成本相同：选新增配对排序列表字典序更小者 [(r0,m1)] < [(r1,m1)]。
    body = payload(
        reviewers=["r0", "r1"],
        manuscripts=["m0", "m1"],
        allowed_pairings=[
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m0"},
        ],
        candidate_pairings=[
            {"reviewer_id": "r1", "manuscript_id": "m1", "cost": 3},
            {"reviewer_id": "r0", "manuscript_id": "m1", "cost": 3},
        ],
    )
    resp = client.post("/repair", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["added_pairings"] == [
        {"reviewer_id": "r0", "manuscript_id": "m1"}
    ]
    assert data["total_cost"] == 3
    assert [(a["reviewer_id"], a["manuscript_id"]) for a in data["assignment"]] == [
        ("r0", "m1"),
        ("r1", "m0"),
    ]


def test_repair_tie_break_not_by_set_size():
    # 一条贵边 vs 两条便宜边同成本：按排序列表字典序而非边数择优。
    # [(r0,m2), (r2,m0)] < [(r2,m2)]，尽管后者更短。
    body = payload(
        candidate_pairings=[
            {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 4},
            {"reviewer_id": "r2", "manuscript_id": "m0", "cost": 2},
            {"reviewer_id": "r0", "manuscript_id": "m2", "cost": 2},
        ],
    )
    resp = client.post("/repair", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["added_pairings"] == [
        {"reviewer_id": "r0", "manuscript_id": "m2"},
        {"reviewer_id": "r2", "manuscript_id": "m0"},
    ]
    assert data["total_cost"] == 4


def test_repair_already_feasible_zero_cost():
    body = payload(
        allowed_pairings=[
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r0", "manuscript_id": "m1"},
            {"reviewer_id": "r1", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m1"},
            {"reviewer_id": "r2", "manuscript_id": "m2"},
        ],
        candidate_pairings=[
            {"reviewer_id": "r2", "manuscript_id": "m0", "cost": 1},
        ],
    )
    resp = client.post("/repair", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["added_pairings"] == []
    assert data["total_cost"] == 0
    # 原图已可行时，assignment 与 /audit 完全一致。
    audit = client.post("/audit", json=audit_view(body))
    assert audit.status_code == 200
    assert data["assignment"] == audit.json()["assignment"]


def test_repair_empty_candidates_allowed():
    # 候选列表可为空：原图可行 -> 200 零成本；不可行 -> 409。
    feasible = payload(
        allowed_pairings=[
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m1"},
            {"reviewer_id": "r2", "manuscript_id": "m2"},
        ],
        candidate_pairings=[],
    )
    resp = client.post("/repair", json=feasible)
    assert resp.status_code == 200, resp.text
    assert resp.json()["added_pairings"] == []
    assert resp.json()["total_cost"] == 0

    infeasible = payload(candidate_pairings=[])
    resp = client.post("/repair", json=infeasible)
    assert resp.status_code == 409, resp.text


def test_repair_assignment_matches_audit_on_repaired_graph():
    # 一致性：把 added_pairings 并入 allowed_pairings 后调 /audit，
    # 所得 assignment 必须与 /repair 返回的完全一致（沿用字典序最小规则）。
    body = payload(
        candidate_pairings=[
            {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 9},
            {"reviewer_id": "r2", "manuscript_id": "m0", "cost": 2},
            {"reviewer_id": "r0", "manuscript_id": "m2", "cost": 2},
            {"reviewer_id": "r1", "manuscript_id": "m2", "cost": 1},
        ],
    )
    repair = client.post("/repair", json=body)
    assert repair.status_code == 200, repair.text
    data = repair.json()
    merged = audit_view(body)
    merged["allowed_pairings"] = merged["allowed_pairings"] + data["added_pairings"]
    audit = client.post("/audit", json=merged)
    assert audit.status_code == 200
    assert data["assignment"] == audit.json()["assignment"]
    # assignment 用到的候选边恰为 added_pairings（正成本 => 不多不少）。
    candidate_set = {
        (c["reviewer_id"], c["manuscript_id"]) for c in body["candidate_pairings"]
    }
    used = {
        (a["reviewer_id"], a["manuscript_id"]) for a in data["assignment"]
    } & candidate_set
    assert used == {
        (p["reviewer_id"], p["manuscript_id"]) for p in data["added_pairings"]
    }


def test_repair_deterministic():
    body = payload(
        reviewers=["r2", "r0", "r1"],
        manuscripts=["m2", "m1", "m0"],
        candidate_pairings=[
            {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 9},
            {"reviewer_id": "r2", "manuscript_id": "m0", "cost": 2},
            {"reviewer_id": "r0", "manuscript_id": "m2", "cost": 2},
        ],
    )
    first = client.post("/repair", json=body)
    second = client.post("/repair", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


# ---------------------------------------------------------------- 409


def test_repair_unrepairable_409_hall_witness():
    # 候选 (r2,m0) 仍不够：r0 与 r2 竞争唯一稿件 m0。
    body = payload(
        candidate_pairings=[
            {"reviewer_id": "r2", "manuscript_id": "m0", "cost": 1},
        ],
    )
    resp = client.post("/repair", json=body)
    assert resp.status_code == 409, resp.text
    data = resp.json()
    assert data["perfect_match_exists"] is False
    # 绝不返回部分成功。
    assert "added_pairings" not in data
    assert "assignment" not in data
    # 合并图上的最大匹配与 Hall 证人。
    assert len(data["maximum_matching"]) == 2
    assert data["hall_witness"] == {
        "reviewer_ids": ["r0", "r2"],
        "neighboring_manuscript_ids": ["m0"],
        "deficiency": 1,
    }


def test_repair_unrepairable_409_deterministic():
    body = payload(candidate_pairings=[])
    first = client.post("/repair", json=body)
    second = client.post("/repair", json=body)
    assert first.status_code == second.status_code == 409
    assert first.json() == second.json()
    assert first.json()["hall_witness"]["reviewer_ids"] == ["r2"]


# ---------------------------------------------------------------- 422


def test_repair_duplicate_candidate_422():
    body = payload(
        candidate_pairings=[
            {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 1},
            {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 2},
        ],
    )
    resp = client.post("/repair", json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail[-1]["loc"] == ["body", "candidate_pairings", 1]
    assert detail[-1]["type"] == "duplicate_candidate_pairing"


def test_repair_candidate_overlaps_allowed_422():
    body = payload(
        candidate_pairings=[
            {"reviewer_id": "r0", "manuscript_id": "m0", "cost": 1},
        ],
    )
    resp = client.post("/repair", json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail[-1]["loc"] == ["body", "candidate_pairings", 0]
    assert detail[-1]["type"] == "candidate_overlaps_allowed"


def test_repair_candidate_unknown_ids_422():
    body = payload(
        candidate_pairings=[
            {"reviewer_id": "ghost", "manuscript_id": "m2", "cost": 1},
            {"reviewer_id": "r2", "manuscript_id": "ghost-m", "cost": 1},
        ],
    )
    resp = client.post("/repair", json=body)
    assert resp.status_code == 422
    locs = {tuple(e["loc"]) for e in resp.json()["detail"]}
    assert ("body", "candidate_pairings", 0, "reviewer_id") in locs
    assert ("body", "candidate_pairings", 1, "manuscript_id") in locs


@pytest.mark.parametrize("bad_cost", [0, -1, -100, 2.5, "3", True, None])
def test_repair_invalid_cost_422(bad_cost):
    body = payload(
        candidate_pairings=[
            {"reviewer_id": "r2", "manuscript_id": "m2", "cost": bad_cost},
        ],
    )
    resp = client.post("/repair", json=body)
    assert resp.status_code == 422
    locs = {tuple(e["loc"]) for e in resp.json()["detail"]}
    assert ("body", "candidate_pairings", 0, "cost") in locs


def test_repair_missing_cost_422():
    body = payload(
        candidate_pairings=[{"reviewer_id": "r2", "manuscript_id": "m2"}],
    )
    resp = client.post("/repair", json=body)
    assert resp.status_code == 422
    locs = {tuple(e["loc"]) for e in resp.json()["detail"]}
    assert ("body", "candidate_pairings", 0, "cost") in locs


def test_repair_missing_candidate_field_422():
    body = payload()
    del body["candidate_pairings"]
    resp = client.post("/repair", json=body)
    assert resp.status_code == 422
    locs = {tuple(e["loc"]) for e in resp.json()["detail"]}
    assert ("body", "candidate_pairings") in locs


def test_repair_candidate_extra_field_422():
    body = payload(
        candidate_pairings=[
            {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 1, "weight": 1},
        ],
    )
    resp = client.post("/repair", json=body)
    assert resp.status_code == 422


def test_repair_base_validation_still_applies():
    # 与 /audit 相同的基础校验在 /repair 上同样生效并聚合候选错误。
    body = payload(
        reviewers=["r0", "r1", "r0"],
        candidate_pairings=[
            {"reviewer_id": "ghost", "manuscript_id": "m2", "cost": 1},
        ],
    )
    resp = client.post("/repair", json=body)
    assert resp.status_code == 422
    locs = {tuple(e["loc"]) for e in resp.json()["detail"]}
    assert ("body", "reviewers", 2) in locs
    assert ("body", "candidate_pairings", 0, "reviewer_id") in locs


# -------------------------------------------- 算法层：暴力枚举交叉验证


def _all_perfect_matchings(reviewers, manuscripts, edges):
    """暴力枚举所有完美匹配，返回按评审人排序的稿件元组集合。"""
    adj = {r: set() for r in reviewers}
    for r, m in edges:
        adj[r].add(m)
    results = set()
    for perm in itertools.permutations(sorted(manuscripts)):
        mapping = dict(zip(sorted(reviewers), perm))
        if all(mapping[r] in adj[r] for r in mapping):
            results.add(tuple(mapping[r] for r in sorted(reviewers)))
    return results


def _brute_force_repair(reviewers, manuscripts, allowed, candidates):
    """暴力求 (最小总成本, 字典序最小的新增排序列表)；不可修复返回 None。"""
    cost_of = {(r, m): c for r, m, c in candidates}
    merged = list(allowed) + [(r, m) for r, m, _ in candidates]
    best = None
    for perm_tuple in _all_perfect_matchings(reviewers, manuscripts, merged):
        edges = set(zip(sorted(reviewers), perm_tuple))
        added = frozenset(e for e in edges if e in cost_of)
        key = (sum(cost_of[e] for e in added), sorted(added))
        if best is None or key < best[0]:
            best = (key, added)
    if best is None:
        return None
    return best[0][0], sorted(best[1])


@pytest.mark.parametrize("seed", range(300))
def test_repair_matches_bruteforce(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 4)
    reviewers = [f"r{i}" for i in range(n)]
    manuscripts = [f"m{i}" for i in range(n)]
    all_edges = [(r, m) for r in reviewers for m in manuscripts]
    rng.shuffle(all_edges)
    split = rng.randint(1, len(all_edges))
    allowed = all_edges[:split]
    candidate_edges = all_edges[split:]
    rng.shuffle(candidate_edges)
    keep = rng.randint(0, len(candidate_edges))
    candidates = [(r, m, rng.randint(1, 5)) for r, m in candidate_edges[:keep]]

    result = run_repair(reviewers, manuscripts, allowed, candidates)
    expected = _brute_force_repair(reviewers, manuscripts, allowed, candidates)

    if expected is None:
        assert result.repairable is False
        audit = result.audit
        assert audit is not None and audit.perfect_match_exists is False
        # 合并全部候选仍无完美匹配，且 Hall 证人缺口等于全局缺口。
        merged = allowed + [(r, m) for r, m, _ in candidates]
        assert not _all_perfect_matchings(reviewers, manuscripts, merged)
        witness = audit.hall_witness
        assert witness is not None
        assert witness.deficiency == n - audit.max_matching_size >= 1
        return

    cost, added = expected
    assert result.repairable is True
    assert result.total_cost == cost
    assert result.added == added

    # assignment 必须是修复后图上字典序最小的完美匹配。
    audit = result.audit
    assert audit is not None and audit.perfect_match_exists is True
    repaired = allowed + added
    canonical = min(_all_perfect_matchings(reviewers, manuscripts, repaired))
    got = {r: m for r, m, *_ in audit.pairs}
    assert tuple(got[r] for r in sorted(reviewers)) == canonical
    # 正成本 => assignment 恰好用完所有新增边。
    assert set(added) <= set(got.items())
