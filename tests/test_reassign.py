"""POST /reassign：发布后撤销/锁定改派的 API 测试与暴力枚举交叉验证。"""

import itertools
import random

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.matching import run_reassign

client = TestClient(app)


def payload(**overrides):
    """基础输入：允许图为完全二部图 K3,3，发布分配为恒等映射。"""
    base = {
        "reviewers": ["r0", "r1", "r2"],
        "manuscripts": ["m0", "m1", "m2"],
        "allowed_pairings": [
            {"reviewer_id": f"r{i}", "manuscript_id": f"m{j}"}
            for i in range(3)
            for j in range(3)
        ],
        "published_assignment": [
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m1"},
            {"reviewer_id": "r2", "manuscript_id": "m2"},
        ],
        "completed_reviewer_ids": [],
        "revoked_pairings": [],
    }
    base.update(overrides)
    return base


def assignment_map(data):
    return {a["reviewer_id"]: a["manuscript_id"] for a in data["assignment"]}


# ---------------------------------------------------------------- 200


def test_no_revocation_keeps_published():
    resp = client.post("/reassign", json=payload())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["perfect_match_exists"] is True
    assert assignment_map(data) == {"r0": "m0", "r1": "m1", "r2": "m2"}
    assert data["changed_reviewer_ids"] == []


def test_reassignment_minimizes_changed_people():
    # 撤销 r0-m0：r0 改用 m2、r2 改用 m0 为 2 人改动；
    # 若 r0->m1、r1->m0 也是 2 人，平局取字典序最小的稿件序列：
    # (m1, m0, m2) < (m2, m1, m0)。
    resp = client.post(
        "/reassign",
        json=payload(revoked_pairings=[{"reviewer_id": "r0", "manuscript_id": "m0"}]),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert assignment_map(data) == {"r0": "m1", "r1": "m0", "r2": "m2"}
    assert data["changed_reviewer_ids"] == ["r0", "r1"]


def test_chain_reassignment_prefers_fewer_changes():
    # 仅保留允许边：r0-{m0,m1}, r1-{m1,m2}, r2-{m0,m2}；
    # 撤销 r0-m0 后，r0->m1 迫使 r1->m2 迫使 r2->m0（3 人改），
    # 这是唯一可行改派。
    edges = [
        ("r0", "m0"), ("r0", "m1"),
        ("r1", "m1"), ("r1", "m2"),
        ("r2", "m0"), ("r2", "m2"),
    ]
    body = payload(
        allowed_pairings=[
            {"reviewer_id": r, "manuscript_id": m} for r, m in edges
        ],
        revoked_pairings=[{"reviewer_id": "r0", "manuscript_id": "m0"}],
    )
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert assignment_map(data) == {"r0": "m1", "r1": "m2", "r2": "m0"}
    assert data["changed_reviewer_ids"] == ["r0", "r1", "r2"]


def test_completed_pair_is_locked():
    # r2 已完成 m2：撤销 r0-m0 后，m2 被锁定占用，r0/r1 只能在 m0/m1 间互换。
    resp = client.post(
        "/reassign",
        json=payload(
            completed_reviewer_ids=["r2"],
            revoked_pairings=[{"reviewer_id": "r0", "manuscript_id": "m0"}],
        ),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    got = assignment_map(data)
    assert got == {"r0": "m1", "r1": "m0", "r2": "m2"}
    assert data["changed_reviewer_ids"] == ["r0", "r1"]
    by_r = {a["reviewer_id"]: a for a in data["assignment"]}
    # 锁定边恒为强制且无替代分配。
    assert by_r["r2"]["forced"] is True
    assert by_r["r2"]["alternative"] is None


def test_completed_reviewer_not_listed_as_changed():
    # r0 锁定 m0、r2 锁定 m2；r1 可用的 m0/m2 均被锁定占用，再撤销 r1-m1，
    # 剩余部分无完美匹配 => 409（已完成者不受影响，也不会出现在缺口里）。
    resp = client.post(
        "/reassign",
        json=payload(
            completed_reviewer_ids=["r0", "r2"],
            revoked_pairings=[{"reviewer_id": "r1", "manuscript_id": "m1"}],
        ),
    )
    assert resp.status_code == 409, resp.text
    data = resp.json()
    assert data["unmatched_reviewer_ids"] == ["r1"]
    assert data["unmatched_manuscript_ids"] == ["m1"]
    assert "r0" not in data["unmatched_reviewer_ids"]
    assert "r2" not in data["unmatched_reviewer_ids"]
    assert data["hall_witness"]["reviewer_ids"] == ["r1"]


def test_alternatives_keep_completed_locked():
    # 非强制边的替代分配也必须保持已完成者的配对不变。
    resp = client.post(
        "/reassign",
        json=payload(completed_reviewer_ids=["r2"]),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    by_r = {a["reviewer_id"]: a for a in data["assignment"]}
    assert by_r["r2"]["forced"] is True
    # r0 非强制（可与 r1 互换）；其替代分配中 r2 仍是 m2。
    assert by_r["r0"]["forced"] is False
    alt = {p["reviewer_id"]: p["manuscript_id"] for p in by_r["r0"]["alternative"]}
    assert alt == {"r0": "m1", "r1": "m0", "r2": "m2"}


def test_everyone_completed_returns_published():
    resp = client.post(
        "/reassign",
        json=payload(
            completed_reviewer_ids=["r0", "r1", "r2"],
            # 撤销其他（非已完成者）允许边无意义；此处撤销一条未使用边，
            # 不属于任何已完成者发布配对，仍然合法。
            revoked_pairings=[{"reviewer_id": "r0", "manuscript_id": "m1"}],
        ),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert assignment_map(data) == {"r0": "m0", "r1": "m1", "r2": "m2"}
    assert data["changed_reviewer_ids"] == []
    assert all(a["forced"] is True for a in data["assignment"])


def test_tie_break_lexicographic_by_sorted_reviewer():
    # 2 人场景：撤销 r0-m0 后两种改派均改 2 人：
    # (m1, m0) 与不存在其他——构造真正平局：
    # 允许图 K2,2，撤 r0-m0 => 唯一改派 (m1,m0)。改为更大平局例：
    # K3,3 撤 r1-m1：可行方案中改动 2 人的有
    #   r0->m1, r1->m0, r2->m2  序列 (m1,m0,m2)
    #   r0->m0, r1->m2, r2->m1  序列 (m0,m2,m1)
    # 字典序：(m0,m2,m1) 更小（第一位 m0 < m1）。
    resp = client.post(
        "/reassign",
        json=payload(revoked_pairings=[{"reviewer_id": "r1", "manuscript_id": "m1"}]),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert assignment_map(data) == {"r0": "m0", "r1": "m2", "r2": "m1"}
    assert sorted(data["changed_reviewer_ids"]) == ["r1", "r2"]


def test_deterministic():
    body = payload(
        reviewers=["r2", "r0", "r1"],
        manuscripts=["m2", "m0", "m1"],
        revoked_pairings=[{"reviewer_id": "r0", "manuscript_id": "m0"}],
    )
    first = client.post("/reassign", json=body)
    second = client.post("/reassign", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_revoked_unused_edge_changes_nothing():
    # 撤销一条发布分配未使用的允许边：发布方案原封不动，零改动。
    resp = client.post(
        "/reassign",
        json=payload(revoked_pairings=[{"reviewer_id": "r0", "manuscript_id": "m2"}]),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert assignment_map(data) == {"r0": "m0", "r1": "m1", "r2": "m2"}
    assert data["changed_reviewer_ids"] == []


# ---------------------------------------------------------------- 409


def test_infeasible_409_on_residual_part():
    # r2 锁定 m2；撤销 r0-m0，且图上 r0 只与 m0、m2 相连：
    # 剩余部分 {r0,r1} 对 {m0,m1} 无完美匹配（r0 两边均不可用）。
    edges = [
        ("r0", "m0"), ("r0", "m2"),
        ("r1", "m0"), ("r1", "m1"),
        ("r2", "m2"),
    ]
    body = payload(
        allowed_pairings=[
            {"reviewer_id": r, "manuscript_id": m} for r, m in edges
        ],
        completed_reviewer_ids=["r2"],
        revoked_pairings=[{"reviewer_id": "r0", "manuscript_id": "m0"}],
    )
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 409, resp.text
    data = resp.json()
    assert data["perfect_match_exists"] is False
    assert "assignment" not in data
    assert "changed_reviewer_ids" not in data
    # 最大匹配/未匹配/Hall 证人都只针对未完成部分：不含 r2、m2。
    matched = {(p["reviewer_id"], p["manuscript_id"]) for p in data["maximum_matching"]}
    assert matched <= {("r0", "m0"), ("r0", "m1"), ("r1", "m0"), ("r1", "m1")}
    assert len(matched) == 1
    assert "r0" in data["unmatched_reviewer_ids"]
    assert "r2" not in data["unmatched_reviewer_ids"]
    assert "m2" not in data["unmatched_manuscript_ids"]
    witness = data["hall_witness"]
    assert "r2" not in witness["reviewer_ids"]
    assert "m2" not in witness["neighboring_manuscript_ids"]
    assert witness["deficiency"] == len(data["unmatched_reviewer_ids"]) == 1


def test_infeasible_409_without_completed():
    body = payload(
        allowed_pairings=[
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m1"},
            {"reviewer_id": "r2", "manuscript_id": "m2"},
        ],
        revoked_pairings=[{"reviewer_id": "r2", "manuscript_id": "m2"}],
    )
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 409, resp.text
    data = resp.json()
    assert data["hall_witness"] == {
        "reviewer_ids": ["r2"],
        "neighboring_manuscript_ids": [],
        "deficiency": 1,
    }
    assert data["unmatched_reviewer_ids"] == ["r2"]
    assert data["unmatched_manuscript_ids"] == ["m2"]


def test_infeasible_409_deterministic():
    body = payload(revoked_pairings=[{"reviewer_id": "r0", "manuscript_id": "m0"}])
    # K3,3 撤一条仍可行；构造不可行：
    body = payload(
        allowed_pairings=[
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m1"},
            {"reviewer_id": "r2", "manuscript_id": "m2"},
        ],
        revoked_pairings=[{"reviewer_id": "r0", "manuscript_id": "m0"}],
    )
    first = client.post("/reassign", json=body)
    second = client.post("/reassign", json=body)
    assert first.status_code == second.status_code == 409
    assert first.json() == second.json()


# ---------------------------------------------------------------- 422


def test_422_published_pair_not_allowed():
    body = payload(
        allowed_pairings=[
            {"reviewer_id": f"r{i}", "manuscript_id": f"m{j}"}
            for i in range(3)
            for j in range(3)
            if (i, j) != (0, 0)
        ]
    )
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail[0]["type"] == "published_pairing_not_allowed"
    assert detail[0]["loc"] == ["body", "published_assignment", 0]


def test_422_published_reviewer_twice():
    body = payload(
        published_assignment=[
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r0", "manuscript_id": "m1"},
            {"reviewer_id": "r2", "manuscript_id": "m2"},
        ]
    )
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422
    types = [e["type"] for e in resp.json()["detail"]]
    assert "published_assignment_not_perfect" in types
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "published_assignment", 1) in locs


def test_422_published_manuscript_twice():
    body = payload(
        published_assignment=[
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m0"},
            {"reviewer_id": "r2", "manuscript_id": "m2"},
        ]
    )
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422
    assert any(
        e["type"] == "published_assignment_not_perfect"
        for e in resp.json()["detail"]
    )


def test_422_published_duplicate_pair():
    body = payload(
        published_assignment=[
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r2", "manuscript_id": "m2"},
        ]
    )
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422
    types = [e["type"] for e in resp.json()["detail"]]
    assert "duplicate_published_pairing" in types


def test_422_published_wrong_length():
    body = payload(
        published_assignment=[
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m1"},
        ]
    )
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert len(detail) == 1
    assert detail[0]["type"] == "published_assignment_not_perfect"
    assert detail[0]["loc"] == ["body", "published_assignment"]


def test_422_published_unknown_ids():
    body = payload(
        published_assignment=[
            {"reviewer_id": "ghost", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "ghost-m"},
            {"reviewer_id": "r2", "manuscript_id": "m2"},
        ]
    )
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422
    locs = {tuple(e["loc"]) for e in resp.json()["detail"]}
    assert ("body", "published_assignment", 0, "reviewer_id") in locs
    assert ("body", "published_assignment", 1, "manuscript_id") in locs


def test_422_revoked_completed_pair():
    body = payload(
        completed_reviewer_ids=["r1"],
        revoked_pairings=[{"reviewer_id": "r1", "manuscript_id": "m1"}],
    )
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail[-1]["type"] == "revoked_completed_pairing"
    assert detail[-1]["loc"] == ["body", "revoked_pairings", 0]


def test_422_revoked_not_in_allowed():
    body = payload(
        allowed_pairings=[
            {"reviewer_id": f"r{i}", "manuscript_id": f"m{j}"}
            for i in range(3)
            for j in range(3)
            if (i, j) != (0, 2)
        ],
        revoked_pairings=[{"reviewer_id": "r0", "manuscript_id": "m2"}],
    )
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail[-1]["type"] == "revoked_pairing_not_allowed"
    assert detail[-1]["loc"] == ["body", "revoked_pairings", 0]


def test_422_revoked_duplicate():
    body = payload(
        revoked_pairings=[
            {"reviewer_id": "r0", "manuscript_id": "m1"},
            {"reviewer_id": "r0", "manuscript_id": "m1"},
        ]
    )
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail[-1]["type"] == "duplicate_revoked_pairing"
    assert detail[-1]["loc"] == ["body", "revoked_pairings", 1]


def test_422_revoked_unknown_ids():
    body = payload(
        revoked_pairings=[
            {"reviewer_id": "ghost", "manuscript_id": "m0"},
            {"reviewer_id": "r0", "manuscript_id": "ghost-m"},
        ]
    )
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422
    locs = {tuple(e["loc"]) for e in resp.json()["detail"]}
    assert ("body", "revoked_pairings", 0, "reviewer_id") in locs
    assert ("body", "revoked_pairings", 1, "manuscript_id") in locs


def test_422_completed_unknown_and_duplicate():
    body = payload(completed_reviewer_ids=["ghost", "r0", "r0"])
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    types = [(e["type"], tuple(e["loc"])) for e in detail]
    assert ("unknown_reviewer", ("body", "completed_reviewer_ids", 0)) in types
    assert (
        "duplicate_completed_reviewer_id",
        ("body", "completed_reviewer_ids", 2),
    ) in types


def test_422_missing_published_assignment():
    body = payload()
    del body["published_assignment"]
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422
    locs = {tuple(e["loc"]) for e in resp.json()["detail"]}
    assert ("body", "published_assignment") in locs


def test_422_empty_published_assignment():
    body = payload(published_assignment=[])
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422


def test_422_optional_fields_default_to_empty():
    # completed_reviewer_ids / revoked_pairings 可省略，等价于空列表。
    body = {
        "reviewers": ["r0", "r1"],
        "manuscripts": ["m0", "m1"],
        "allowed_pairings": [
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m1"},
        ],
        "published_assignment": [
            {"reviewer_id": "r0", "manuscript_id": "m0"},
            {"reviewer_id": "r1", "manuscript_id": "m1"},
        ],
    }
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed_reviewer_ids"] == []


def test_422_extra_field():
    body = payload(nope=1)
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422


def test_422_empty_completed_id():
    body = payload(completed_reviewer_ids=["   "])
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422
    locs = {tuple(e["loc"]) for e in resp.json()["detail"]}
    # Pydantic field_validator 对整个列表报错（与 reviewers/manuscripts 一致）。
    assert ("body", "completed_reviewer_ids") in locs


def test_base_validation_still_applies():
    body = payload(
        reviewers=["r0", "r1", "r0"],
        completed_reviewer_ids=["ghost"],
        revoked_pairings=[{"reviewer_id": "r9", "manuscript_id": "m9"}],
    )
    resp = client.post("/reassign", json=body)
    assert resp.status_code == 422
    locs = {tuple(e["loc"]) for e in resp.json()["detail"]}
    assert ("body", "reviewers", 2) in locs
    assert ("body", "completed_reviewer_ids", 0) in locs
    assert ("body", "revoked_pairings", 0, "reviewer_id") in locs


# -------------------------------------------- 算法层：暴力枚举交叉验证


def _all_perfect_matchings(reviewers, manuscripts, edges):
    adj = {r: set() for r in reviewers}
    for r, m in edges:
        adj[r].add(m)
    results = set()
    for perm in itertools.permutations(sorted(manuscripts)):
        mapping = dict(zip(sorted(reviewers), perm))
        if all(mapping[r] in adj[r] for r in mapping):
            results.add(tuple(mapping[r] for r in sorted(reviewers)))
    return results


def _bruteforce_reassign(reviewers, manuscripts, allowed, published, completed, revoked):
    """暴力求最优改派；不可行返回 None。

    锁定已完成者后，在剩余子图枚举全部完美匹配，比较
    (改动人数, 按评审人升序的稿件序列)。
    """
    published_of = dict(published)
    locked = {r: published_of[r] for r in completed}
    locked_ms = set(locked.values())
    revoked_set = set(revoked)
    free = sorted(set(reviewers) - set(completed))
    free_ms = sorted(set(manuscripts) - locked_ms)
    edges = [
        (r, m)
        for r, m in allowed
        if (r, m) not in revoked_set and r not in locked and m not in locked_ms
    ]
    perfects = _all_perfect_matchings(free, free_ms, edges)
    if not perfects:
        return None
    best = None
    for seq in perfects:
        mapping = dict(zip(free, seq))
        full = dict(locked)
        full.update(mapping)
        changed = tuple(
            sorted(r for r in reviewers if full[r] != published_of[r])
        )
        key = (len(changed), tuple(full[r] for r in sorted(reviewers)))
        if best is None or key < best[0]:
            best = (key, full, changed)
    return best[1], list(best[2])


@pytest.mark.parametrize("seed", range(400))
def test_reassign_matches_bruteforce(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 4)
    reviewers = [f"r{i}" for i in range(n)]
    manuscripts = [f"m{i}" for i in range(n)]
    all_edges = [(r, m) for r in reviewers for m in manuscripts]
    rng.shuffle(all_edges)
    keep = rng.randint(n, len(all_edges))
    allowed = all_edges[:keep]

    # 仅当允许图存在完美匹配时才能生成已发布分配。
    perfects = _all_perfect_matchings(reviewers, manuscripts, allowed)
    if not perfects:
        return
    published_seq = rng.choice(sorted(perfects))
    published = list(zip(sorted(reviewers), published_seq))

    completed = sorted(rng.sample(reviewers, rng.randint(0, n)))
    completed_set = set(completed)
    # 撤销边：从允许边中选，避开已完成者的发布配对。
    revocable = [
        e for e in allowed if not (e[0] in completed_set and dict(published)[e[0]] == e[1])
    ]
    rng.shuffle(revocable)
    revoked = revocable[: rng.randint(0, len(revocable))]

    result = run_reassign(
        reviewers, manuscripts, allowed, published, completed, revoked
    )
    expected = _bruteforce_reassign(
        reviewers, manuscripts, allowed, published, completed, revoked
    )

    if expected is None:
        assert result.feasible is False
        assert result.hall_witness is not None
        # Hall 证人不变量：邻居恰为证人评审人在剩余子图中的全部邻居。
        locked_ms = {dict(published)[r] for r in completed}
        revoked_set = set(revoked)
        residual_edges = {
            (r, m)
            for r, m in allowed
            if (r, m) not in revoked_set
            and r not in completed_set
            and m not in locked_ms
        }
        w = result.hall_witness
        true_neighbors = {
            m for r, m in residual_edges if r in set(w.reviewer_ids)
        }
        assert set(w.neighboring_manuscript_ids) == true_neighbors
        assert w.deficiency == len(w.reviewer_ids) - len(true_neighbors)
        assert w.deficiency == len(result.unmatched_reviewers) >= 1
        # 最大匹配合法且已完成者不出现在未完成部分。
        assert all((r, m) in residual_edges for r, m in result.pairs)
        assert all(r not in completed_set for r, _ in result.pairs)
        return

    expected_map, expected_changed = expected
    assert result.feasible is True
    got = {r: m for r, m, *_ in result.pairs}
    assert got == expected_map
    assert result.changed_reviewer_ids == expected_changed
    # 已完成者绝不改动，也绝不列入 changed。
    for r in completed:
        assert got[r] == dict(published)[r]
        assert r not in result.changed_reviewer_ids
    # 所有分配边均为未撤销且未占用的允许边（或锁定边）。
    locked_ms = {dict(published)[r] for r in completed}
    for r, m in got.items():
        assert (r, m) in allowed
        if r in completed_set:
            assert (r, m) not in set(revoked)
        else:
            assert (r, m) not in set(revoked)
            assert m not in locked_ms


@pytest.mark.parametrize("seed", range(100))
def test_reassign_forced_annotations(seed):
    """强制标注相对「锁定已完成者」后的可行解集；替代分配保持锁定。"""
    rng = random.Random(10_000 + seed)
    n = rng.randint(1, 4)
    reviewers = [f"r{i}" for i in range(n)]
    manuscripts = [f"m{i}" for i in range(n)]
    all_edges = [(r, m) for r in reviewers for m in manuscripts]
    rng.shuffle(all_edges)
    allowed = all_edges[: rng.randint(n, len(all_edges))]
    perfects = _all_perfect_matchings(reviewers, manuscripts, allowed)
    if not perfects:
        return
    published = list(zip(sorted(reviewers), rng.choice(sorted(perfects))))
    completed = sorted(rng.sample(reviewers, rng.randint(0, n)))
    completed_set = set(completed)
    locked_ms = {dict(published)[r] for r in completed}
    revocable = [
        e
        for e in allowed
        if not (e[0] in completed_set and dict(published)[e[0]] == e[1])
    ]
    rng.shuffle(revocable)
    revoked = revocable[: rng.randint(0, len(revocable))]

    result = run_reassign(
        reviewers, manuscripts, allowed, published, completed, revoked
    )
    if not result.feasible:
        return

    # 剩余可行完美匹配集合（补回锁定边即完整分配）。
    free = sorted(set(reviewers) - completed_set)
    free_ms = sorted(set(manuscripts) - locked_ms)
    revoked_set = set(revoked)
    edges = [
        (r, m)
        for r, m in allowed
        if (r, m) not in revoked_set and r not in completed_set and m not in locked_ms
    ]
    free_perfects = _all_perfect_matchings(free, free_ms, edges)
    full_perfects = set()
    for seq in free_perfects:
        full = dict(zip(free, seq))
        full.update({r: dict(published)[r] for r in completed})
        full_perfects.add(tuple(full[r] for r in sorted(reviewers)))

    by_r = {r: (m, forced, alt) for r, m, forced, alt in result.pairs}
    for index, r in enumerate(sorted(reviewers)):
        m, forced, alt = by_r[r]
        truly_forced = all(seq[index] == m for seq in full_perfects)
        assert forced is truly_forced
        if r in completed_set:
            assert forced is True and alt is None
        if not forced:
            assert alt is not None
            alt_map = dict(alt)
            alt_seq = tuple(alt_map[r] for r in sorted(reviewers))
            assert alt_seq in full_perfects
            assert alt_map[r] != m
