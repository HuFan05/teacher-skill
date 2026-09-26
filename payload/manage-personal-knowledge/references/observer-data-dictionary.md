# manage-personal-knowledge Observer阶段字典

目录版本为`manage-personal-knowledge/v1`，指标契约为`timing/v1`。工作流阶段用于比较模型与工具往返中的实际步骤；脚本阶段只记录生产入口，不记录测试、一次性迁移脚本或纯工具库。

## 阶段

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
| `manage-personal-knowledge.script.run` | 记录一个正常生产脚本入口的完整子进程墙钟时间。 |

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

禁止记录`path`、`title`、`note_title`、`query`、`body`、`excerpt`、`heading`、`diff`、`command`、`exception_text`、`resource_id`、`raw_file_hash`和`tool_output`。包装器失败或超时时，不得改变原命令的标准输出、标准错误、退出码或文件行为。


## 生产入口叶阶段

| 阶段 | 边界 |
| --- | --- |
| `manage-personal-knowledge.script.manage_kb` | 记录`manage_kb.py`作为正常生产入口时的完整子进程墙钟时间。 |

调用包装器时优先使用与目标入口同名的阶段；只有目录中没有该入口时才使用`manage-personal-knowledge.script.run`。`manage_kb.py`包装器把隐私安全的子命令名（例如`paper-locate`或`index`）记录到既有`operation`字段，不建立新的子命令阶段，也不记录查询或路径。这些阶段区分正常入口，不为测试、一次性迁移脚本或纯工具库创建记录。
