"""FastAPI 应用：POST /audit、POST /repair 与 POST /reassign。

/audit：
- 200：存在完美匹配，返回字典序最小的确定性分配及逐对强制性。
- 409：不存在完美匹配，返回最大匹配、未匹配双方 ID 与 Hall 定理证人
  （无部分成功）。
- 422：重复 ID、重复配对、未知引用、数量不等、空列表等，错误定位到具体字段/下标。

/repair：在 /audit 输入基础上额外接受带正整数成本的候选配对，
仅允许补入候选边，求使完美匹配成立的最小总成本方案
（成本相同时按新增配对排序列表字典序择优）：
- 200：返回 added_pairings、total_cost 与修复后图上的完整 assignment
  （沿用 /audit 的字典序最小规则）；原图已可行时返回成本 0 的空补充。
- 409：合并全部候选仍不可行，复用合并图上的 Hall 定理证人解释缺口。
- 422：候选配对重复、与原边重叠、未知 ID、非正整数成本等，精确定位。

/reassign：在 /audit 输入基础上接受已发布分配、已完成评审人 ID 与
撤销配对，在「未撤销且未被已完成者占用」的配对上重新求完整分配：
- 200：返回完整 assignment 与 changed_reviewer_ids；先使相对已发布
  分配的改动人数最少，平局按评审人 ID 升序的稿件 ID 序列字典序最小；
  已完成者的配对锁定不变。
- 409：锁定已完成者后剩余部分无法完美匹配，返回剩余子图上的最大匹配、
  未匹配双方 ID 与 Hall 证人（不返回部分成功）。
- 422：已发布分配不是原允许对上的完美匹配、撤销配对不属于原允许配对、
  撤销/改动已完成者配对、未知或重复引用等，错误精确定位字段/下标。
"""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .matching import AuditResult, run_audit, run_reassign, run_repair
from .schemas import (
    AssignedPair,
    AuditRequest,
    AuditResponse,
    ConflictResponse,
    HallWitness,
    OutputPair,
    ReassignRequest,
    ReassignResponse,
    RepairRequest,
    RepairResponse,
)

app = FastAPI(
    title="匿名评审分配可辨识性审计 API",
    description=(
        "基于二部图完美匹配，判定每条配对是否在所有完美匹配中被强制选择；"
        "可求使完美匹配成立的最小成本候选边补充方案；"
        "并支持发布后撤销配对、锁定已完成评审人的最小改动改派。"
    ),
    version="1.2.0",
)


def _error(
    loc: list[str | int],
    msg: str,
    err_type: str,
    value: object,
) -> dict:
    return {"type": err_type, "loc": ["body", *loc], "msg": msg, "input": value}


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    # jsonable_encoder 会剔除 Pydantic 放在 ctx 中的不可序列化异常对象。
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors())},
    )


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "service": "review-assignment-identifiability-audit",
        "docs": "/docs",
        "endpoints": ["/audit", "/repair", "/reassign"],
    }


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"status": "ok"}


def _assignment_entries(result: AuditResult) -> list[AssignedPair]:
    """把算法层成功结果组装为带强制性标注的分配列表。"""
    assignment: list[AssignedPair] = []
    for reviewer_id, manuscript_id, forced, alternative in result.pairs:
        assignment.append(
            AssignedPair(
                reviewer_id=reviewer_id,
                manuscript_id=manuscript_id,
                forced=forced,
                alternative=(
                    None
                    if forced
                    else [
                        OutputPair(reviewer_id=r, manuscript_id=m)
                        for r, m in alternative
                    ]
                ),
            )
        )
    return assignment


def _conflict_response(result: AuditResult, detail: str) -> JSONResponse:
    """由算法层失败结果组装 409 响应（含 Hall 定理证人）。"""
    conflict = _conflict_model(result, detail)
    # JSONResponse 以携带 409；模型经 model_dump 序列化，schema 已在 responses 声明。
    return JSONResponse(status_code=409, content=conflict.model_dump())


def _conflict_model(result: AuditResult, detail: str) -> ConflictResponse:
    """由算法层失败结果组装 409 响应体（含 Hall 定理证人）。"""
    witness = result.hall_witness
    # 409 分支由算法层保证证人存在；防御性断言。
    assert witness is not None
    return ConflictResponse(
        detail=detail,
        maximum_matching=[
            OutputPair(reviewer_id=r, manuscript_id=m) for r, m in result.pairs
        ],
        unmatched_reviewer_ids=result.unmatched_reviewers,
        unmatched_manuscript_ids=result.unmatched_manuscripts,
        hall_witness=HallWitness(
            reviewer_ids=witness.reviewer_ids,
            neighboring_manuscript_ids=witness.neighboring_manuscript_ids,
            deficiency=witness.deficiency,
        ),
    )


@app.post(
    "/audit",
    response_model=AuditResponse,
    responses={
        409: {"model": ConflictResponse, "description": "不存在完美匹配"},
        422: {"description": "输入校验失败"},
    },
)
async def audit(request: AuditRequest) -> AuditResponse | ConflictResponse:
    errors = _validate_semantics(request)
    if errors:
        raise RequestValidationError(errors)

    pairings = [(p.reviewer_id, p.manuscript_id) for p in request.allowed_pairings]
    result = run_audit(
        reviewers=request.reviewers,
        manuscripts=request.manuscripts,
        pairings=pairings,
    )

    if not result.perfect_match_exists:
        return _conflict_response(  # type: ignore[return-value]
            result,
            "不存在完美匹配；以下为基数最大的匹配、未匹配双方及 Hall 定理证人。",
        )
    return AuditResponse(assignment=_assignment_entries(result))


@app.post(
    "/repair",
    response_model=RepairResponse,
    responses={
        409: {"model": ConflictResponse, "description": "合并全部候选仍不存在完美匹配"},
        422: {"description": "输入校验失败"},
    },
)
async def repair(request: RepairRequest) -> RepairResponse | ConflictResponse:
    errors = _validate_semantics(request) + _validate_candidates(request)
    if errors:
        raise RequestValidationError(errors)

    pairings = [(p.reviewer_id, p.manuscript_id) for p in request.allowed_pairings]
    candidates = [
        (c.reviewer_id, c.manuscript_id, c.cost)
        for c in request.candidate_pairings
    ]
    result = run_repair(
        reviewers=request.reviewers,
        manuscripts=request.manuscripts,
        pairings=pairings,
        candidates=candidates,
    )

    if not result.repairable:
        # 不可修复分支由 run_repair 保证 audit 存在；防御性断言。
        assert result.audit is not None
        return _conflict_response(  # type: ignore[return-value]
            result.audit,
            "即使补入全部候选配对仍不存在完美匹配；"
            "以下为合并全部候选后的基数最大匹配、未匹配双方及 Hall 定理证人。",
        )

    assert result.audit is not None
    return RepairResponse(
        added_pairings=[
            OutputPair(reviewer_id=r, manuscript_id=m) for r, m in result.added
        ],
        total_cost=result.total_cost,
        assignment=_assignment_entries(result.audit),
    )


@app.post(
    "/reassign",
    response_model=ReassignResponse,
    responses={
        409: {"model": ConflictResponse, "description": "锁定已完成者后剩余部分不存在完美匹配"},
        422: {"description": "输入校验失败"},
    },
)
async def reassign(request: ReassignRequest) -> ReassignResponse | ConflictResponse:
    errors = _validate_semantics(request) + _validate_reassign(request)
    if errors:
        raise RequestValidationError(errors)

    pairings = [(p.reviewer_id, p.manuscript_id) for p in request.allowed_pairings]
    published = [
        (p.reviewer_id, p.manuscript_id) for p in request.published_assignment
    ]
    revoked = [
        (p.reviewer_id, p.manuscript_id) for p in request.revoked_pairings
    ]
    result = run_reassign(
        reviewers=request.reviewers,
        manuscripts=request.manuscripts,
        pairings=pairings,
        published=published,
        completed=request.completed_reviewer_ids,
        revoked=revoked,
    )

    if not result.feasible:
        # 复用 409 结构：最大匹配/未匹配 ID/Hall 证人均针对未完成部分
        # （剩余子图），锁定配对不混入其中。
        conflict = _conflict_model(
            AuditResult(
                perfect_match_exists=False,
                pairs=result.pairs,
                max_matching_size=len(result.pairs),
                unmatched_reviewers=result.unmatched_reviewers,
                unmatched_manuscripts=result.unmatched_manuscripts,
                hall_witness=result.hall_witness,
            ),
            "撤销配对并锁定已完成者后，未完成部分不存在完美匹配；"
            "以下为该部分基数最大的匹配、未匹配双方及 Hall 定理证人"
            "（已完成者的锁定配对不受影响，未包含在内）。",
        )
        return JSONResponse(  # type: ignore[return-value]
            status_code=409, content=conflict.model_dump()
        )

    return ReassignResponse(
        assignment=_assignment_entries(
            AuditResult(
                perfect_match_exists=True,
                pairs=result.pairs,
                max_matching_size=len(result.pairs),
            )
        ),
        changed_reviewer_ids=result.changed_reviewer_ids,
    )


def _validate_semantics(request: AuditRequest) -> list[dict]:
    """跨字段语义校验，返回错误列表（空列表表示通过）。"""
    errors: list[dict] = []

    # 评审人与稿件数量必须相等。
    if len(request.reviewers) != len(request.manuscripts):
        errors.append(
            _error(
                ["manuscripts"],
                f"评审人与稿件数量必须相等："
                f"{len(request.reviewers)} != {len(request.manuscripts)}",
                "count_mismatch",
                len(request.manuscripts),
            )
        )

    # 重复评审人 ID（定位到每个重复出现的位置）。
    seen: set[str] = set()
    for index, reviewer_id in enumerate(request.reviewers):
        if reviewer_id in seen:
            errors.append(
                _error(
                    ["reviewers", index],
                    f"评审人 ID 重复：{reviewer_id!r}",
                    "duplicate_reviewer_id",
                    reviewer_id,
                )
            )
        seen.add(reviewer_id)

    # 重复稿件 ID。
    seen.clear()
    for index, manuscript_id in enumerate(request.manuscripts):
        if manuscript_id in seen:
            errors.append(
                _error(
                    ["manuscripts", index],
                    f"稿件 ID 重复：{manuscript_id!r}",
                    "duplicate_manuscript_id",
                    manuscript_id,
                )
            )
        seen.add(manuscript_id)

    reviewer_set = set(request.reviewers)
    manuscript_set = set(request.manuscripts)

    # 未知引用与重复配对（每条配对分别定位）。
    seen_pairs: set[tuple[str, str]] = set()
    for index, pairing in enumerate(request.allowed_pairings):
        if pairing.reviewer_id not in reviewer_set:
            errors.append(
                _error(
                    ["allowed_pairings", index, "reviewer_id"],
                    f"未知评审人引用：{pairing.reviewer_id!r} 不在 reviewers 列表中",
                    "unknown_reviewer",
                    pairing.reviewer_id,
                )
            )
        if pairing.manuscript_id not in manuscript_set:
            errors.append(
                _error(
                    ["allowed_pairings", index, "manuscript_id"],
                    f"未知稿件引用：{pairing.manuscript_id!r} 不在 manuscripts 列表中",
                    "unknown_manuscript",
                    pairing.manuscript_id,
                )
            )
        key = (pairing.reviewer_id, pairing.manuscript_id)
        if key in seen_pairs:
            errors.append(
                _error(
                    ["allowed_pairings", index],
                    f"配对重复：({pairing.reviewer_id!r}, {pairing.manuscript_id!r})",
                    "duplicate_pairing",
                    {"reviewer_id": key[0], "manuscript_id": key[1]},
                )
            )
        seen_pairs.add(key)

    return errors


def _validate_candidates(request: RepairRequest) -> list[dict]:
    """候选配对的跨字段语义校验（/repair 专用），返回错误列表。"""
    errors: list[dict] = []

    reviewer_set = set(request.reviewers)
    manuscript_set = set(request.manuscripts)
    allowed_pairs = {
        (p.reviewer_id, p.manuscript_id) for p in request.allowed_pairings
    }

    seen: set[tuple[str, str]] = set()
    for index, candidate in enumerate(request.candidate_pairings):
        if candidate.reviewer_id not in reviewer_set:
            errors.append(
                _error(
                    ["candidate_pairings", index, "reviewer_id"],
                    f"未知评审人引用：{candidate.reviewer_id!r} 不在 reviewers 列表中",
                    "unknown_reviewer",
                    candidate.reviewer_id,
                )
            )
        if candidate.manuscript_id not in manuscript_set:
            errors.append(
                _error(
                    ["candidate_pairings", index, "manuscript_id"],
                    f"未知稿件引用：{candidate.manuscript_id!r} 不在 manuscripts 列表中",
                    "unknown_manuscript",
                    candidate.manuscript_id,
                )
            )
        key = (candidate.reviewer_id, candidate.manuscript_id)
        if key in seen:
            errors.append(
                _error(
                    ["candidate_pairings", index],
                    f"候选配对重复：({key[0]!r}, {key[1]!r})",
                    "duplicate_candidate_pairing",
                    {"reviewer_id": key[0], "manuscript_id": key[1]},
                )
            )
        if key in allowed_pairs:
            errors.append(
                _error(
                    ["candidate_pairings", index],
                    f"候选配对与允许配对重叠：({key[0]!r}, {key[1]!r})",
                    "candidate_overlaps_allowed",
                    {"reviewer_id": key[0], "manuscript_id": key[1]},
                )
            )
        seen.add(key)

    return errors


def _validate_reassign(request: ReassignRequest) -> list[dict]:
    """已发布分配 / 已完成者 / 撤销配对的跨字段语义校验（/reassign 专用）。"""
    errors: list[dict] = []

    reviewer_set = set(request.reviewers)
    manuscript_set = set(request.manuscripts)
    allowed_pairs = {
        (p.reviewer_id, p.manuscript_id) for p in request.allowed_pairings
    }

    # ---- 已完成评审人：未知引用 + 重复 ID ----
    seen_completed: set[str] = set()
    for index, reviewer_id in enumerate(request.completed_reviewer_ids):
        if reviewer_id not in reviewer_set:
            errors.append(
                _error(
                    ["completed_reviewer_ids", index],
                    f"未知评审人引用：{reviewer_id!r} 不在 reviewers 列表中",
                    "unknown_reviewer",
                    reviewer_id,
                )
            )
        if reviewer_id in seen_completed:
            errors.append(
                _error(
                    ["completed_reviewer_ids", index],
                    f"已完成评审人 ID 重复：{reviewer_id!r}",
                    "duplicate_completed_reviewer_id",
                    reviewer_id,
                )
            )
        seen_completed.add(reviewer_id)

    # ---- 已发布分配：未知引用、重复配对（含两侧各自重复）、必须是允许对上的完美匹配 ----
    published_pairs: list[tuple[str, str]] = []
    seen_pub_reviewer: set[str] = set()
    seen_pub_manuscript: set[str] = set()
    seen_pub_pair: set[tuple[str, str]] = set()
    for index, pairing in enumerate(request.published_assignment):
        loc_pair = ["published_assignment", index]
        if pairing.reviewer_id not in reviewer_set:
            errors.append(
                _error(
                    ["published_assignment", index, "reviewer_id"],
                    f"未知评审人引用：{pairing.reviewer_id!r} 不在 reviewers 列表中",
                    "unknown_reviewer",
                    pairing.reviewer_id,
                )
            )
        if pairing.manuscript_id not in manuscript_set:
            errors.append(
                _error(
                    ["published_assignment", index, "manuscript_id"],
                    f"未知稿件引用：{pairing.manuscript_id!r} 不在 manuscripts 列表中",
                    "unknown_manuscript",
                    pairing.manuscript_id,
                )
            )
        key = (pairing.reviewer_id, pairing.manuscript_id)
        if key in seen_pub_pair:
            errors.append(
                _error(
                    loc_pair,
                    f"已发布配对重复：({key[0]!r}, {key[1]!r})",
                    "duplicate_published_pairing",
                    {"reviewer_id": key[0], "manuscript_id": key[1]},
                )
            )
        if pairing.reviewer_id in seen_pub_reviewer:
            errors.append(
                _error(
                    loc_pair,
                    f"已发布分配中评审人出现多次：{pairing.reviewer_id!r}",
                    "published_assignment_not_perfect",
                    {"reviewer_id": key[0], "manuscript_id": key[1]},
                )
            )
        if pairing.manuscript_id in seen_pub_manuscript:
            errors.append(
                _error(
                    loc_pair,
                    f"已发布分配中稿件出现多次：{pairing.manuscript_id!r}",
                    "published_assignment_not_perfect",
                    {"reviewer_id": key[0], "manuscript_id": key[1]},
                )
            )
        # 配对必须属于原允许配对（引用均已知时才比对，避免级联误报）。
        if (
            pairing.reviewer_id in reviewer_set
            and pairing.manuscript_id in manuscript_set
            and key not in allowed_pairs
        ):
            errors.append(
                _error(
                    loc_pair,
                    f"已发布配对不在允许配对中：({key[0]!r}, {key[1]!r})",
                    "published_pairing_not_allowed",
                    {"reviewer_id": key[0], "manuscript_id": key[1]},
                )
            )
        seen_pub_pair.add(key)
        seen_pub_reviewer.add(pairing.reviewer_id)
        seen_pub_manuscript.add(pairing.manuscript_id)
        published_pairs.append(key)

    # 完美匹配的基数校验（仅在无重复/未知引用时报告，避免与上面的定位重复）。
    if not errors and len(published_pairs) != len(request.reviewers):
        missing_reviewers = sorted(reviewer_set - {r for r, _ in published_pairs})
        missing_manuscripts = sorted(
            manuscript_set - {m for _, m in published_pairs}
        )
        errors.append(
            _error(
                ["published_assignment"],
                "已发布分配必须是完美匹配：每位评审人与每篇稿件各恰好出现一次；"
                f"缺少评审人 {missing_reviewers}，缺少稿件 {missing_manuscripts}",
                "published_assignment_not_perfect",
                [
                    {"reviewer_id": r, "manuscript_id": m}
                    for r, m in published_pairs
                ],
            )
        )

    published_of = {r: m for r, m in published_pairs}

    # ---- 撤销配对：未知引用、自相重复、必须属于原允许配对、不得撤销已完成者配对 ----
    seen_revoked: set[tuple[str, str]] = set()
    for index, pairing in enumerate(request.revoked_pairings):
        loc_pair = ["revoked_pairings", index]
        if pairing.reviewer_id not in reviewer_set:
            errors.append(
                _error(
                    ["revoked_pairings", index, "reviewer_id"],
                    f"未知评审人引用：{pairing.reviewer_id!r} 不在 reviewers 列表中",
                    "unknown_reviewer",
                    pairing.reviewer_id,
                )
            )
        if pairing.manuscript_id not in manuscript_set:
            errors.append(
                _error(
                    ["revoked_pairings", index, "manuscript_id"],
                    f"未知稿件引用：{pairing.manuscript_id!r} 不在 manuscripts 列表中",
                    "unknown_manuscript",
                    pairing.manuscript_id,
                )
            )
        key = (pairing.reviewer_id, pairing.manuscript_id)
        if key in seen_revoked:
            errors.append(
                _error(
                    loc_pair,
                    f"撤销配对重复：({key[0]!r}, {key[1]!r})",
                    "duplicate_revoked_pairing",
                    {"reviewer_id": key[0], "manuscript_id": key[1]},
                )
            )
        if (
            pairing.reviewer_id in reviewer_set
            and pairing.manuscript_id in manuscript_set
            and key not in allowed_pairs
        ):
            errors.append(
                _error(
                    loc_pair,
                    f"撤销配对不属于原允许配对：({key[0]!r}, {key[1]!r})",
                    "revoked_pairing_not_allowed",
                    {"reviewer_id": key[0], "manuscript_id": key[1]},
                )
            )
        # 已完成者的发布配对不得撤销（其完成状态足以定位，即使发布分配本身有误）。
        if (
            pairing.reviewer_id in seen_completed
            and published_of.get(pairing.reviewer_id) == pairing.manuscript_id
        ):
            errors.append(
                _error(
                    loc_pair,
                    f"已完成评审人 {key[0]!r} 的配对 "
                    f"({key[0]!r}, {key[1]!r}) 不得撤销",
                    "revoked_completed_pairing",
                    {"reviewer_id": key[0], "manuscript_id": key[1]},
                )
            )
        seen_revoked.add(key)

    return errors
