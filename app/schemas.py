"""请求与响应的 Pydantic 模型。"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Pairing(BaseModel):
    """一条允许的（评审人，稿件）配对。"""

    model_config = ConfigDict(extra="forbid")

    reviewer_id: str = Field(..., description="评审人 ID")
    manuscript_id: str = Field(..., description="稿件 ID")

    @field_validator("reviewer_id", "manuscript_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("ID 不能为空字符串")
        return value


class AuditRequest(BaseModel):
    """POST /audit 的请求体。三组列表均非空，评审人与稿件数量相等。"""

    model_config = ConfigDict(extra="forbid")

    reviewers: list[str] = Field(..., min_length=1, description="评审人 ID 列表（非空）")
    manuscripts: list[str] = Field(..., min_length=1, description="稿件 ID 列表（非空）")
    allowed_pairings: list[Pairing] = Field(
        ..., min_length=1, description="允许配对列表（非空）"
    )

    @field_validator("reviewers", "manuscripts")
    @classmethod
    def _ids_non_empty(cls, values: list[str]) -> list[str]:
        for index, value in enumerate(values):
            if not isinstance(value, str) or not value or not value.strip():
                raise ValueError(
                    f"位置 {index} 的 ID 不能为空字符串"
                )
        return values


class CandidatePairing(Pairing):
    """一条可补入的候选配对，带正整数成本。

    成本使用严格整数：布尔、浮点、字符串一律拒绝（422）。
    """

    cost: int = Field(..., strict=True, gt=0, description="补入该配对的成本（正整数）")


class RepairRequest(AuditRequest):
    """POST /repair 的请求体：在 /audit 输入基础上增加候选配对列表。"""

    candidate_pairings: list[CandidatePairing] = Field(
        ...,
        description="可补入的候选配对（可为空）；不得与 allowed_pairings 重叠、"
        "不得自相重复，成本为正整数",
    )


class OutputPair(BaseModel):
    """审计输出中的单条配对。"""

    reviewer_id: str
    manuscript_id: str


class AssignedPair(BaseModel):
    """确定性分配中的一条配对及其强制属性。

    - ``forced`` 为 true 时，该边出现在每一个完美匹配中（必然选择）。
    - ``forced`` 为 false 时，``alternative`` 给出一组**完整的**替代分配，
      该替代分配也是完美匹配，且不含本条配对。
    """

    reviewer_id: str
    manuscript_id: str
    forced: bool
    alternative: Optional[list[OutputPair]] = Field(
        default=None,
        description="forced=false 时为不含本边的一组完整替代分配",
    )


class AuditResponse(BaseModel):
    """存在完美匹配时的成功响应。"""

    perfect_match_exists: bool = True
    assignment: list[AssignedPair] = Field(
        description="按评审人 ID 排序后字典序最小的确定性分配，并逐对标注强制性"
    )


class RepairResponse(BaseModel):
    """修复成功响应：最小成本补充方案及修复后的完整分配。"""

    perfect_match_exists: bool = True
    added_pairings: list[OutputPair] = Field(
        description="需补入的候选配对，按 (reviewer_id, manuscript_id) 排序；"
        "总成本最小，成本相同时该排序列表字典序最小；原图已可行时为空"
    )
    total_cost: int = Field(description="补入配对的最小总成本（原图已可行时为 0）")
    assignment: list[AssignedPair] = Field(
        description="修复后图（允许配对 + 新增配对）上的字典序最小确定性分配，"
        "并逐对标注强制性"
    )


class HallWitness(BaseModel):
    """Hall 定理证人：一组评审人，其可评稿件总数少于人数。

    - ``neighboring_manuscript_ids`` 恰为 ``reviewer_ids`` 在原
      ``allowed_pairings`` 中的全部邻居（不多不少）；
    - ``deficiency`` = 评审人数 − 邻居数，恒等于全局未匹配评审人数；
    - 两个 ID 列表均按 ID 升序，相同输入结果稳定。
    """

    reviewer_ids: list[str] = Field(
        description="违反 Hall 条件的评审人子集（已排序，非空）"
    )
    neighboring_manuscript_ids: list[str] = Field(
        description="上述评审人在原允许配对中的全部邻居（已排序，可为空）"
    )
    deficiency: int = Field(
        description="评审人数 − 邻居数，等于全局未匹配评审人数（≥1）"
    )


class ConflictResponse(BaseModel):
    """不存在完美匹配时的 409 响应（绝不返回部分成功）。"""

    perfect_match_exists: bool = False
    detail: str
    maximum_matching: list[OutputPair] = Field(
        description="基数最大的确定性匹配（完整展开，非配对结果）"
    )
    unmatched_reviewer_ids: list[str] = Field(description="未匹配的评审人 ID（已排序）")
    unmatched_manuscript_ids: list[str] = Field(description="未匹配的稿件 ID（已排序）")
    hall_witness: HallWitness = Field(
        description="Hall 定理证人：由交替路径构造的缺口子集及其全部邻居"
    )
