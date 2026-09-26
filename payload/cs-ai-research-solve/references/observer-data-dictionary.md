# cs-ai-research-solve Observer阶段字典

目录版本为`cs-ai-research-solve/v1`，指标契约为`timing/v1`。脚本阶段为空时，仍由工作流阶段记录整个Skill的分段墙钟时间。

## 工作流阶段

| 阶段 | 含义 |
| --- | --- |
| `workflow.startup` | 记录该标准工作流阶段的墙钟区间；只在实际进入时设置。 |
| `retrieve` | 记录该标准工作流阶段的墙钟区间；只在实际进入时设置。 |
| `verify_live` | 记录该标准工作流阶段的墙钟区间；只在实际进入时设置。 |
| `plan_change` | 记录该标准工作流阶段的墙钟区间；只在实际进入时设置。 |
| `mutate` | 记录该标准工作流阶段的墙钟区间；只在实际进入时设置。 |
| `validate` | 记录该标准工作流阶段的墙钟区间；只在实际进入时设置。 |
| `version_control` | 记录该标准工作流阶段的墙钟区间；只在实际进入时设置。 |
| `final_response` | 记录该标准工作流阶段的墙钟区间；只在实际进入时设置。 |

## 允许字段

| 字段 | 类型 | 边界 |
| --- | --- | --- |
| `mode` | `name` | 只记录不含业务内容的分类或非负计数。 |
| `operation` | `name` | 只记录不含业务内容的分类或非负计数。 |
| `write` | `boolean` | 只记录不含业务内容的分类或非负计数。 |
| `file_count` | `nonnegative_integer` | 只记录不含业务内容的分类或非负计数。 |
| `candidate_count` | `nonnegative_integer` | 只记录不含业务内容的分类或非负计数。 |
| `byte_count` | `nonnegative_integer` | 只记录不含业务内容的分类或非负计数。 |
| `harness_call_count` | `nonnegative_integer` | 只记录不含业务内容的分类或非负计数。 |
| `result_count` | `nonnegative_integer` | 只记录不含业务内容的分类或非负计数。 |
| `change_count` | `nonnegative_integer` | 只记录不含业务内容的分类或非负计数。 |
| `warning_count` | `nonnegative_integer` | 只记录不含业务内容的分类或非负计数。 |
| `error_count` | `nonnegative_integer` | 只记录不含业务内容的分类或非负计数。 |

不得记录路径、标题、查询词、正文、片段、heading、diff、命令、异常文本、资源ID、业务文件哈希或工具输出。Observer失效时不得改变Skill或脚本的输出、退出码和文件行为。

## 当前脚本阶段

| 阶段 | 真实范围 |
| --- | --- |
| `cs-ai-research-solve.script.crs` | Current public asset CLI. Stored records and explicit reviews determine state; telemetry never grants research authority. |
| `cs-ai-research-solve.script.crs_replay` | Bounded reproduction supervisor. Saves execution evidence and cleans owned workspaces; execution does not admit research review. |
| `cs-ai-research-solve.script.validate_terminology` | Current terminology and documented CLI checks only; no judgment of research claims. |

此可选宿主观测与 CRS 研究工作流的20分钟诊断阈值分别记录，不增加研究停止条件或审核权限。
