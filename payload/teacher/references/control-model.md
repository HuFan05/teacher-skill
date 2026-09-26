# 控制模型

## 谁拥有回合

控制权的第一个问题是：谁决定“这一轮发生什么”。宿主 agent 驱动对话、用自己的工具调研；
判断与门禁属于 Skill：

```
请求原话
  → frame-check                需求框定：封闭分类与锚定的读法
  → 规则：问不问、问什么         need.decide：最多问一次封闭选择题
  → 宿主调研                    agent 用自己的工具；先查研究资产与本地索引
  → 答案草稿                    只填声明的槽位
  → check-answer → 答案门       全部是可判定的规则；被引片段逐字节重读
  → 确定性渲染                  交付给用户文字的唯一写者
  → 原子提交                    框定、回答、证据、归档候选一次写入
```

模型只在两个封闭接口上参与：框定的分类与读法、答案的槽位。流程判断没有一项交给它。

## 流程判断不外包

下列判断**全部**由确定性规则做出，模型没有任何方法能影响它们：

| 判断 | 谁做 |
| --- | --- |
| 这个请求要不要反问用户、问什么、问几次 | `need.decide` + 渲染器固定话术 |
| 先查哪些资产 | 研究资产与本地索引，总在向外检索之前 |
| 一项操作能不能执行、执行到哪里为止 | `broker.validate_request`、`confine`、网络策略、声明命令 |
| 能读到哪里为止 | `config.read_roots` 与路径包含判定；符号链接指出去也会被拒 |
| 这类问题能不能不查来源就回答 | `constants.EVIDENCE_REQUIRED_KINDS` + 最低调研规则 |
| 一个答案能不能显示 | `answer.validate_answer` |
| 什么成为归档候选、如何排序 | `archive.candidates_from_answer` |
| 核心认知怎么更新 | `cognition.derive`（从存储重算） |
| 一个结论能否晋升、是否完成 | `Guard.check_promotion`、`Engine._completion_issues` |
| 一次维修是否消耗评审轮次 | `acceptance.AcceptanceGate.advance` |

人只做两类决定：**方向与价值**（我到底想问哪一种、要不要归档、要不要把它升格为研究目标），
以及**不可逆或对外的动作**（绑定目标、写入笔记库、删除）。每一类都被压成一次封闭选择。

## 只读的边界

`th/broker.py` 是执行侧：它按配置给出的读取根、声明命令与网络策略工作，自己不扩大任何一项。
操作词表全部只读：

| 操作 | 参数 | 返回 |
| --- | --- | --- |
| `search_library` | `query` | 已归档条目的候选（id、类型、摘要）与未展示数 |
| `read_library` | `item_id` | 一条已归档条目 |
| `search_local` | `query` | 本地索引的候选（section_id、路径、标题、≤200 字摘要）与未展示数 |
| `read_section` | `section_id` | 一个片段的**当前**原文（重新读取磁盘文件；变更与过期如实标注） |
| `arxiv_search` | `query` | 论文标题、作者、日期、摘要 |
| `fetch_url` | `url` | 白名单网页的正文片段 |
| `run_declared` | `name`、`arguments` | 返回码、输出摘要与输出尾部片段 |
| `hash_file` / `stat_file` / `count_lines` | `relative_path` | 摘要、字节数、行数 |

硬性质：词表里没有会改动的操作；参数键封闭，多一个键就拒绝；命令只能点名；
网络关闭时两项网络操作一律拒绝；原始输出只以有界片段进入数据包，不上屏。

宿主 agent 自带工具，能读、能写、能联网——它不在这张词表之内。Skill 能约束的是自己执行的读取，
以及**进入答案的每一条证据**：片段要能在原位置被重新读到，读不到就记为 `declared`，核对不上就丢弃。

## 两个 head

| head | 内容 | 谁能推进 |
| --- | --- | --- |
| `authority` | 被接受的目标、归档的研究资产、晋升的结论、终局完成 | 绑定目标、归档、晋升、终局确认 |
| `execution` | 需求框定、回答、证据片段、归档候选、窗口、检查点 | 每一次通过检查的回答 |

一轮回答、一个检查点、一次评审只推进 `execution`。因为 `authority` 从这些方法里不可达，
“继续工作从而改变了结论”不是一条需要谁记住的规则——它无法被表达。

每个 head 都是一条哈希链；`Store.verify_chain()` 能走一遍父链，缺环与悬空父节点都会被报出。

## 提交协议

所有结构性改动都经过 `Store.transact()`：操作重放检测（同一 `operation_id` 带不同请求 → `operation_reused`）、
比较并交换（`expected` 必须等于当前修订，否则 `stale_snapshot`）、提交后回读校验。
**在任何拒绝路径上，两个 head 都保持原值。** 观测（评估、失败票据、修复记录）走 `Store.observe()`，不推进 head。

## 阶段验收

| 层级 | 规则 |
| --- | --- |
| 一次回答 | 结构或答案门不通过 → 返回封闭的拒绝码；`check-answer` 退出码 3，不写入任何状态 |
| 一次调研 | 深度决定轮数上限；用尽如实报告，不是失败 |
| 证据类问题 | 必须先尝试过检索才能宣布“可以回答”；所有渠道都查过仍无来源，才以“来源不足”作答 |
| 大型交付（长文、研究地图、批量笔记修改） | `AcceptanceGate`：先做确定性检查（机械问题不消耗评审轮次），再由独立复核判定一份冻结清单；最多三轮；第三轮仍失败时进入 `accepted_after_three_reviews` 后备，**不得**写成复核通过；复核不可用不计一轮 |
| 终局完成 | `assess` 只由封闭值生成评估；通过的评估绑定当时两个修订，之后再有工作即失效；可选的独立复核只能否决 |

## 检查点与评审必须是挣来的

- 检查点的 `verified` 是**计算**出来的：只有它引用的每个证据编号都指向 Skill 自己核验过的证据，
  或指向已被接受的结论，才算已核验。只声明“已核验”而引用不到这样的证据，会被记为 `unverifiable`。
- 评审按评审方计数：`human`/`local`（操作者）与 `independent_model`（Skill 自己发起的隔离复核）被计数；
  `declared_independent`（宿主声称独立）被计数但保持可区分，因为软件无法认证；`self` 与 `foreign` 只记录，从不计数。
- `review-claim --independent` 让 Skill 用只含该结论及其证据片段的数据包调用一次隔离复核；
  `PASS` 且确定性检查无风险才记为接受，`FAIL` 记为拒绝，`INCONCLUSIVE` 或不可用什么都不记。

## 完成门（持续研究目标）

| 码 | 含义 |
| --- | --- |
| `NO_VERIFIED_CHECKPOINT` | 没有任何已核验的检查点 |
| `NO_VERIFIED_CLAIM` | 目标结论尚未被接受 |
| `DEPENDENCY_OPEN` | 强关系依赖链未闭合，或存在环 |
| `CANNOT_IMPLY_MISSING` | 未写明该结论不能推出什么 |
| `SCOPE_UNDECLARED` | 未声明适用范围 |
| `GRADE_INSUFFICIENT` | 证据等级不足以支撑该强度 |
| `REPRODUCTION_BLOCKED` | 复现被阻断 |
| `UNREVIEWED_INSIGHT` | 把未经核验的洞见当作结论 |
| `ROUTE_PORTFOLIO_INVALID` | 仍有开放的瓶颈或障碍节点 |
| `ASSESSMENT_STALE` | 评估早于最近一次状态推进 |
| `COVERAGE_INCOMPLETE` | 覆盖计数未闭合 |
| `REVIEW_DENIED` | 复核方否决 |

完成状态按证据等级判定：`ROUTE_READY`（`bounded_empirical`）、`REPRODUCIBLE`（`exact_reproduction`）、
`VERIFIED`（`formal` 或 `certificate`）；存在任一阻止条件即为 `NOT_COMPLETE`。

## 多路线窗口（可选）

需要并行比较几条真正不同的路线时，可以用 `engine open-window` 冻结恰好三条在
`learning_signal`、`architecture_family`、`compute_regime` 三个轴上都不同的路线，三个尝试在一次 CAS 中一起建立。
这是研究者主动选择的比较工具，**不是**提问或研究的前置步骤；Skill 不规定研究的轮次、路线数或思考顺序。

## 覆盖计数

`coverage-plan` / `coverage-check` 要求一次评估对一个可数集合逐项给出处置，并报告：
要求几项、已处置几项、仍待定的是哪些、筛查**没看**几项（`omitted_candidates`），
以及两条固定声明：`software_cannot_authenticate_reading: true`、`semantic_completeness_proven: false`。

## 委托与澄清

- 常规、可逆的选择在已确立范围内**自主决定**，不转交给用户；采用的默认前提写在回答开头。
- 缺失的实现偏好**不自动**变成要问用户的问题。
- 只有当“未解决的选择会实质改变工作方向”、且调查无法解决时，才提出**一次**最小的封闭选择。
- 事实上的不确定通过调查或诚实地说“不知道”来处理，**不是**拿去表决。
- 价值判断与方向选择属于人。Skill 能做的是先调查、把问题压到最小，而不是替人拍板。
