# 匿名评审分配可辨识性审计 API

给定评审人、稿件及二者之间的允许配对（二部图），本服务：

1. 判断是否存在**完美匹配**（每位评审人恰好评审一篇稿件，每篇稿件恰好被评审一次）；
2. 若存在，返回一个**确定性分配**：按评审人 ID 升序排序后，分配序列字典序最小
   （未匹配位置视为 `null` 且小于任何稿件 ID；完美匹配下所有评审人均被匹配）；
3. 对该分配中的**每一对**判断其是否为**强制配对**——即在所有完美匹配中都必然被选择；
4. 对每个非强制配对，附一组**完整的替代分配**：它本身也是完美匹配，且不包含被审计的这对边。

此外，`POST /repair` 在相同输入基础上接受一组带**正整数成本**的候选配对，
求**仅补入候选边**即可使完美匹配成立的**最小总成本**方案：

- 成本相同时，按新增配对 `(reviewer_id, manuscript_id)` 排序后的列表作字典序择优；
- 返回 `added_pairings`、`total_cost` 与修复后图上的完整 `assignment`
  （沿用上述字典序最小规则）；原图已可行时返回成本 0 的空补充。

状态码约定：

- 存在完美匹配（或修复成功） → `200`
- 不存在完美匹配（或合并全部候选仍不可行） → `409`，返回基数最大的匹配、
  未匹配的评审人/稿件 ID，以及一份 **Hall 定理证人**（`hall_witness`），**不返回部分成功**
- 重复 ID、重复配对、未知引用、数量不等、空列表、候选与原边重叠、非正整数成本等
  输入问题 → `422`，错误定位到具体字段与下标

技术栈：Python · FastAPI · Pydantic v2 · NetworkX（Hopcroft–Karp + 交替有向图强连通分量
+ Edmonds 开花算法最小权完美匹配）。

---

## 单条命令启动（依赖随容器安装）

```bash
docker compose up --build
```

服务地址（监听 **8000** 端口）：

- API 根地址：<http://localhost:8000/>
- 审计端点：`POST http://localhost:8000/audit`
- 修复端点：`POST http://localhost:8000/repair`
- 交互式文档（Swagger UI）：<http://localhost:8000/docs>
- OpenAPI 模式：<http://localhost:8000/openapi.json>
- 健康检查：<http://localhost:8000/healthz>

> 若使用不带 compose 子命令的旧版 Docker，请改用 `docker-compose up --build`。
> 容器镜像基于 `python:3.11-slim`，构建时通过 `requirements.txt` 安装全部依赖，无需本机准备 Python 环境。

后台运行：`docker compose up --build -d`；停止：`docker compose down`。

---

## 请求格式

`POST /audit`，`Content-Type: application/json`：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `reviewers` | `string[]` | 非空、无重复、无空字符串 |
| `manuscripts` | `string[]` | 非空、无重复、无空字符串，数量与 `reviewers` 相等 |
| `allowed_pairings` | `{reviewer_id, manuscript_id}[]` | 非空、无重复配对；引用的 ID 必须分别存在于上面两个列表 |

额外字段会被拒绝（422）。

### 示例请求

```bash
curl -s -X POST http://localhost:8000/audit \
  -H 'Content-Type: application/json' \
  -d '{
    "reviewers": ["r0", "r1", "r2"],
    "manuscripts": ["m0", "m1", "m2"],
    "allowed_pairings": [
      {"reviewer_id": "r0", "manuscript_id": "m0"},
      {"reviewer_id": "r0", "manuscript_id": "m1"},
      {"reviewer_id": "r1", "manuscript_id": "m0"},
      {"reviewer_id": "r1", "manuscript_id": "m1"},
      {"reviewer_id": "r2", "manuscript_id": "m2"}
    ]
  }'
```

### 200 响应（存在完美匹配）

```json
{
  "perfect_match_exists": true,
  "assignment": [
    {
      "reviewer_id": "r0",
      "manuscript_id": "m0",
      "forced": false,
      "alternative": [
        {"reviewer_id": "r0", "manuscript_id": "m1"},
        {"reviewer_id": "r1", "manuscript_id": "m0"},
        {"reviewer_id": "r2", "manuscript_id": "m2"}
      ]
    },
    {
      "reviewer_id": "r1",
      "manuscript_id": "m1",
      "forced": false,
      "alternative": [
        {"reviewer_id": "r0", "manuscript_id": "m1"},
        {"reviewer_id": "r1", "manuscript_id": "m0"},
        {"reviewer_id": "r2", "manuscript_id": "m2"}
      ]
    },
    {
      "reviewer_id": "r2",
      "manuscript_id": "m2",
      "forced": true,
      "alternative": null
    }
  ]
}
```

字段含义：

- `assignment` 按 `reviewer_id` 升序排列，为字典序最小的确定性完美匹配；
- `forced=true` 表示该对出现在**所有**完美匹配中（评审身份可辨识/无法匿名化），此时 `alternative` 为 `null`；
- `forced=false` 时 `alternative` 给出一组**完整替代分配**（覆盖全部评审人），
  它是另一个合法完美匹配且不含当前这对边——证明该配对不是强制的。

上例中 `r2–m2` 是唯一可行选择，故为强制配对；`r0/r1` 可互换，故二者均非强制。

### 409 响应（不存在完美匹配）

```json
{
  "perfect_match_exists": false,
  "detail": "不存在完美匹配；以下为基数最大的匹配、未匹配双方及 Hall 定理证人。",
  "maximum_matching": [
    {"reviewer_id": "r0", "manuscript_id": "m0"},
    {"reviewer_id": "r1", "manuscript_id": "m1"}
  ],
  "unmatched_reviewer_ids": ["r2"],
  "unmatched_manuscript_ids": ["m2"],
  "hall_witness": {
    "reviewer_ids": ["r2"],
    "neighboring_manuscript_ids": [],
    "deficiency": 1
  }
}
```

`maximum_matching` 为完整展开的基数最大匹配（确定性规则：同样按评审人 ID 升序、
字典序最小），并分别给出两侧无法匹配的 ID。响应不包含 `assignment`，
不存在“部分成功”。

`hall_witness` 是 **Hall 定理证人**——一组可评稿件总数少于人数的评审人，
直接解释了为何不可能完美分配：

- `reviewer_ids`：违反 Hall 条件的评审人子集 S（按 ID 升序，非空）；
- `neighboring_manuscript_ids`：S 在原 `allowed_pairings` 中的**全部**邻居
  （按 ID 升序，可为空）——不多不少，恰为这群评审人所有可评的稿件；
- `deficiency`：`|S| − |N(S)|`，恒等于全局未匹配评审人数
  （即 `unmatched_reviewer_ids` 的长度）。

证人由确定性最大匹配出发、沿交替路径构造：从未匹配评审人开始，
沿「非匹配边 评审人→稿件、匹配边 稿件→评审人」遍历所有可达节点，
其中的评审人即 S、稿件即 N(S)。相同输入必得相同证人。

一个非平凡例子（`r0` 未匹配，但它自己有两个邻居，单看并不违规；
沿交替路径可达 `r1`、`r2` 后才暴露出 3 人共享 2 篇稿件的缺口）：

```bash
curl -s -X POST http://localhost:8000/audit \
  -H 'Content-Type: application/json' \
  -d '{
    "reviewers": ["r0", "r1", "r2"],
    "manuscripts": ["m0", "m1", "m2"],
    "allowed_pairings": [
      {"reviewer_id": "r0", "manuscript_id": "m0"},
      {"reviewer_id": "r0", "manuscript_id": "m1"},
      {"reviewer_id": "r1", "manuscript_id": "m0"},
      {"reviewer_id": "r2", "manuscript_id": "m1"}
    ]
  }'
```

```json
{
  "perfect_match_exists": false,
  "detail": "不存在完美匹配；以下为基数最大的匹配、未匹配双方及 Hall 定理证人。",
  "maximum_matching": [
    {"reviewer_id": "r1", "manuscript_id": "m0"},
    {"reviewer_id": "r2", "manuscript_id": "m1"}
  ],
  "unmatched_reviewer_ids": ["r0"],
  "unmatched_manuscript_ids": ["m2"],
  "hall_witness": {
    "reviewer_ids": ["r0", "r1", "r2"],
    "neighboring_manuscript_ids": ["m0", "m1"],
    "deficiency": 1
  }
}
```

### 422 响应（输入校验失败）

错误以数组形式返回于 `detail`，每条含 `type` / `loc` / `msg` / `input`，
`loc` 精确定位到字段与列表下标，例如重复配对：

```json
{
  "detail": [
    {
      "type": "duplicate_pairing",
      "loc": ["body", "allowed_pairings", 1],
      "msg": "配对重复：('r0', 'm0')",
      "input": {"reviewer_id": "r0", "manuscript_id": "m0"}
    },
    {
      "type": "unknown_reviewer",
      "loc": ["body", "allowed_pairings", 2, "reviewer_id"],
      "msg": "未知评审人引用：'ghost' 不在 reviewers 列表中",
      "input": "ghost"
    }
  ]
}
```

| 错误类型 | 触发条件 | `loc` 定位 |
| --- | --- | --- |
| `count_mismatch` | 评审人与稿件数量不等 | `body.manuscripts` |
| `duplicate_reviewer_id` | 评审人 ID 重复 | `body.reviewers.<下标>` |
| `duplicate_manuscript_id` | 稿件 ID 重复 | `body.manuscripts.<下标>` |
| `duplicate_pairing` | 允许配对重复 | `body.allowed_pairings.<下标>` |
| `unknown_reviewer` | 配对引用了不存在的评审人 | `body.allowed_pairings.<下标>.reviewer_id` |
| `unknown_manuscript` | 配对引用了不存在的稿件 | `body.allowed_pairings.<下标>.manuscript_id` |
| `missing` / `string_too_short` | 字段缺失、列表为空（Pydantic 内建） | 对应字段 |
| `value_error` | ID 为空串/纯空白（Pydantic 内建） | 对应字段 |

一次请求会聚合返回所有可定位的错误。

---

## POST /repair（最小成本修复）

在 `/audit` 的三个字段之外，请求体额外接受 `candidate_pairings`：
一组带**正整数成本**的候选配对。服务求出**仅补入候选边**即可使完美匹配
成立的**最小总成本**方案；成本相同时，按新增配对
`(reviewer_id, manuscript_id)` 排序后的列表作字典序择优。

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `reviewers` | `string[]` | 非空、无重复、无空字符串 |
| `manuscripts` | `string[]` | 非空、无重复、无空字符串，数量与 `reviewers` 相等 |
| `allowed_pairings` | `{reviewer_id, manuscript_id}[]` | 非空、无重复配对；引用的 ID 必须分别存在于上面两个列表 |
| `candidate_pairings` | `{reviewer_id, manuscript_id, cost}[]` | 必填、可为空列表；`cost` 为正整数；不得与 `allowed_pairings` 重叠、不得自相重复；引用的 ID 必须已知 |

### 示例请求

```bash
curl -s -X POST http://localhost:8000/repair \
  -H 'Content-Type: application/json' \
  -d '{
    "reviewers": ["r0", "r1", "r2"],
    "manuscripts": ["m0", "m1", "m2"],
    "allowed_pairings": [
      {"reviewer_id": "r0", "manuscript_id": "m0"},
      {"reviewer_id": "r1", "manuscript_id": "m1"}
    ],
    "candidate_pairings": [
      {"reviewer_id": "r2", "manuscript_id": "m2", "cost": 9},
      {"reviewer_id": "r2", "manuscript_id": "m0", "cost": 2},
      {"reviewer_id": "r0", "manuscript_id": "m2", "cost": 2}
    ]
  }'
```

### 200 响应（修复成功）

```json
{
  "perfect_match_exists": true,
  "added_pairings": [
    {"reviewer_id": "r0", "manuscript_id": "m2"},
    {"reviewer_id": "r2", "manuscript_id": "m0"}
  ],
  "total_cost": 4,
  "assignment": [
    {"reviewer_id": "r0", "manuscript_id": "m2", "forced": true, "alternative": null},
    {"reviewer_id": "r1", "manuscript_id": "m1", "forced": true, "alternative": null},
    {"reviewer_id": "r2", "manuscript_id": "m0", "forced": true, "alternative": null}
  ]
}
```

字段含义：

- `added_pairings`：需补入的候选配对，按 `(reviewer_id, manuscript_id)` 排序。
  上例中直接补 `r2–m2` 需成本 9，而补 `r0–m2` 与 `r2–m0` 两条仅需 4，故选后者；
- `total_cost`：新增配对的最小总成本；**原图已可行时返回空列表与 0**；
- `assignment`：修复后图（允许配对 + 新增配对）上的字典序最小确定性分配，
  结构与 `/audit` 完全一致（含 `forced` / `alternative` 标注）。
  由于成本为正，该分配恰好用完所有新增配对，不多不少。

### 409 响应（合并全部候选仍不可行）

```json
{
  "perfect_match_exists": false,
  "detail": "即使补入全部候选配对仍不存在完美匹配；以下为合并全部候选后的基数最大匹配、未匹配双方及 Hall 定理证人。",
  "maximum_matching": [
    {"reviewer_id": "r1", "manuscript_id": "m1"},
    {"reviewer_id": "r2", "manuscript_id": "m0"}
  ],
  "unmatched_reviewer_ids": ["r0"],
  "unmatched_manuscript_ids": ["m2"],
  "hall_witness": {
    "reviewer_ids": ["r0", "r2"],
    "neighboring_manuscript_ids": ["m0"],
    "deficiency": 1
  }
}
```

结构与 `/audit` 的 409 相同，但所有数量都基于**合并全部候选配对后的图**：
`hall_witness` 解释了即使补入全部候选仍存在的缺口（上例中 `r0` 与 `r2`
合起来仍只有 `m0` 一篇可评）。响应不包含 `added_pairings` 与 `assignment`。

### 422 响应（候选配对的校验）

在 `/audit` 的全部校验之上，候选配对额外引入以下错误类型
（`cost` 的非正整数/非整数校验由 Pydantic 在 schema 阶段完成）：

| 错误类型 | 触发条件 | `loc` 定位 |
| --- | --- | --- |
| `duplicate_candidate_pairing` | 候选配对自相重复（无论成本是否相同） | `body.candidate_pairings.<下标>` |
| `candidate_overlaps_allowed` | 候选与 `allowed_pairings` 中已有配对重叠 | `body.candidate_pairings.<下标>` |
| `unknown_reviewer` | 候选引用了不存在的评审人 | `body.candidate_pairings.<下标>.reviewer_id` |
| `unknown_manuscript` | 候选引用了不存在的稿件 | `body.candidate_pairings.<下标>.manuscript_id` |
| `greater_than` / `int_type` | 成本非正整数（0、负数、浮点、字符串、布尔） | `body.candidate_pairings.<下标>.cost` |

示例（候选与允许配对重叠 + 候选自相重复 + 未知引用，一次聚合返回）：

```json
{
  "detail": [
    {
      "type": "candidate_overlaps_allowed",
      "loc": ["body", "candidate_pairings", 0],
      "msg": "候选配对与允许配对重叠：('r0', 'm0')",
      "input": {"reviewer_id": "r0", "manuscript_id": "m0"}
    },
    {
      "type": "duplicate_candidate_pairing",
      "loc": ["body", "candidate_pairings", 2],
      "msg": "候选配对重复：('r2', 'm2')",
      "input": {"reviewer_id": "r2", "manuscript_id": "m2"}
    },
    {
      "type": "unknown_reviewer",
      "loc": ["body", "candidate_pairings", 3, "reviewer_id"],
      "msg": "未知评审人引用：'ghost' 不在 reviewers 列表中",
      "input": "ghost"
    }
  ]
}
```

---

## 算法说明

- **最大匹配 / 完美匹配判定**：NetworkX 的 Hopcroft–Karp
  （`networkx.bipartite.hopcroft_karp_matching`），二部图两侧节点用
  `("reviewer", id)`、`("manuscript", id)` 区分，ID 同名也不冲突。
- **字典序最小确定性分配**：按评审人 ID 升序贪心，每位评审人优先不匹配
  （`null` 最小），否则在候选稿件中按 ID 升序选择「仍能在剩余子图达成目标基数」者；
  每步以一次 Hopcroft–Karp 作可行性预言，因此结果对相同输入完全确定。
- **强制性判定**：对完美匹配 M 构造交替有向图（匹配边 `稿件→评审人`，非匹配边
  `评审人→稿件`）。由 Berge 交替环定理，匹配边非强制 ⇔ 它位于某个 M-交替环上
  ⇔ 两端在同一强连通分量中；一次 SCC 分解（`networkx.strongly_connected_components`）
  即可求出全部强制边。
- **替代分配**：对非强制边，在交替有向图中用邻接有序的 BFS 找最短交替环，
  环上匹配/非匹配边身份翻转，得到一个完整、合法、确定性且不含被审计边的完美匹配。
- **Hall 定理证人（409）**：取字典序最小的最大匹配 M，从未匹配评审人出发，
  在同一交替有向图中遍历可达节点集 Z。Z 中的稿件必已全部匹配
  （否则存在增广路，与最大性矛盾），故取 S = Z ∩ 评审人时，
  N(S) 恰为 Z ∩ 稿件，且 `|S| − |N(S)|` = 未匹配评审人数 = 全局缺口
  （Kőnig–Ore 定理）。源点与邻接均按 ID 排序，可达集与遍历顺序无关，
  证人对相同输入完全确定。
- **最小成本修复（/repair）**：允许边权重视为 0、候选边权重视为其成本时，
  「最小成本完美匹配」用到的候选边集合即最小成本补充方案（成本为正，多补无益）。
  平局择优：候选边按 `(reviewer_id, manuscript_id)` 排序后第 i 条（共 k 条）赋权
  `cost·2^k − 2^(k−1−i)`，允许边赋权 0——主目标最小化总成本，次目标最大化
  「按序是否纳入」指示向量的字典序，后者等价于新增配对排序列表字典序最小
  （正成本保证同成本方案不会互为前缀）。一次最小权完美匹配即可同时确定两个层级，
  且所有最优解的候选边集合相同，结果对相同输入完全确定。最小权完美匹配由
  Edmonds 开花算法完成（`networkx.max_weight_matching`，纯 Python，权重取负，
  `maxcardinality=True` 保证完美匹配）。
- **修复后的 assignment**：在「允许配对 + 新增配对」图上复用上述全部规则
  （字典序最小分配 + 强制性标注）；由正成本可证该分配恰好用完所有新增边。
  合并全部候选仍不可行时，在合并图上复用 Hall 证人构造，作为 409 的解释。

适用规模：典型会议量级（两侧各 ≤100、每人若干候选）亚秒级完成；
数百规模也可在数秒级返回。

## 项目结构

```
app/
  __init__.py
  main.py        # FastAPI 路由（/audit、/repair）、422/409 语义校验与响应组装
  matching.py    # 二部图、确定性匹配、SCC 强制性、交替环替代分配、Hall 证人、最小成本修复
  schemas.py     # Pydantic 请求/响应模型
tests/
  test_api.py    # HTTP 层：200/409/422、Hall 证人与错误定位
  test_matching.py# 算法层：200 组随机图全排列暴力交叉验证 + Hall 证人不变量
  test_repair.py # /repair：最小成本、平局字典序、422 定位 + 300 组暴力交叉验证
Dockerfile
docker-compose.yml
requirements.txt
```

## 本地（非 Docker）开发与测试

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install pytest httpx
python3 -m pytest -q
uvicorn app.main:app --reload --port 8000
```
