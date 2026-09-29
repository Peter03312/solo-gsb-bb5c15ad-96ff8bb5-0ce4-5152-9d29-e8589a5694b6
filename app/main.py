"""FastAPI 应用：POST /audit 与 POST /repair。

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
"""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .matching import AuditResult, run_audit, run_repair
from .schemas import (
    AssignedPair,
    AuditRequest,
    AuditResponse,
    ConflictResponse,
    HallWitness,
    OutputPair,
    RepairRequest,
    RepairResponse,
)

app = FastAPI(
    title="匿名评审分配可辨识性审计 API",
    description=(
        "基于二部图完美匹配，判定每条配对是否在所有完美匹配中被强制选择；"
        "并可求使完美匹配成立的最小成本候选边补充方案。"
    ),
    version="1.1.0",
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
        "endpoints": ["/audit", "/repair"],
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
    witness = result.hall_witness
    # 409 分支由算法层保证证人存在；防御性断言。
    assert witness is not None
    conflict = ConflictResponse(
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
    # JSONResponse 以携带 409；模型经 model_dump 序列化，schema 已在 responses 声明。
    return JSONResponse(status_code=409, content=conflict.model_dump())


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
