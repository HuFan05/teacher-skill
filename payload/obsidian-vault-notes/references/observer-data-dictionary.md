# Obsidian 运行阶段数据字典

本文件解释`obsidian-vault-notes/v1`阶段。Observer 的通用字段、计算公式和 CLI 参数以宿主提供的 Observer CLI（`SKILL_OBSERVER_CLI`）自身的说明为准；机器校验读取同目录的`observer-phases.json`。

## 工作流阶段

| 阶段 | 起点与终点 | 可以判断什么 | 高占比时先查什么 |
| --- | --- | --- | --- |
| `workflow.startup` | Skill 被选中后，到第一次检索或业务动作前 | 启动后的准备时间 | 规则读取范围、首次计划和必须加载的参考文件 |
| `retrieve` | 开始定位笔记，到得到候选或目标笔记 | 检索流程耗时 | 索引状态、刷新、查询和候选数量 |
| `verify_live` | 开始读取当前磁盘文件，到确认正文和元数据 | 当前文件核验耗时 | 文件读取、标题解析、链接元数据 |
| `plan_change` | 已确认当前文件，到形成可执行编辑计划 | 编辑前的模型判断时间 | 是否重复读取、是否可合并检索和审计 |
| `mutate` | 开始 dry-run 或变换，到写入或确认不写 | 变换和写盘时间 | 编辑脚本、资源引用预检、原子写入 |
| `validate` | 写入或 dry-run 完成，到 lint 和语义检查完成 | 验证时间 | lint 扫描范围、语义哨兵、重复全文件读取 |
| `version_control` | 开始窄范围 Git 审计，到状态、diff 和 diff-check 完成 | Git 检查时间 | 仓库规模、pathspec 是否过宽、Git 进程启动 |
| `final_response` | 最后一次业务检查完成，到根 Skill 发出`end`请求 | 回答整理时间 | 模型生成、重复汇总和过长的输出要求 |

`workflow.startup`只能说明“第一次业务动作前发生了多少时间”。它不能单独证明时间花在规则读取；启动后的模型规划和其他准备也在这里。

当`workflow.startup`是`begin`后的首个工作流阶段时，它从显式`begin`时间开始，以免漏掉同一调用里阶段记录命令自身的启动和传输时间；它不包含`pre_begin`。

工作流代码还包括`retrieve`、`verify_live`、`plan_change`、`mutate`、`validate`、`version_control`和`final_response`。这些名称没有`workflow.`前缀，脚本不能自行改名。

## 检索脚本

`obsidian.retrieval.validate`记录参数和授权范围校验。`index.status`读取索引新鲜度。`index.refresh.enumerate`枚举 Markdown 文件，`classify`判断增删改，`parse`解析正文和元数据，`commit`写入 SQLite。`query`只包围实际候选检索子进程；`note.resolve`按路径、标题和别名解析目标；`note.live_read`读取当前磁盘文件；`note.link_metadata`整理直接链接元数据；`result.build`是结果组装的包含式父阶段，`result.build.candidates`只记录候选裁剪和字符预算组装。

若刷新叶阶段占比高，先查 Vault 文件数、解析成本和 SQLite 事务。若`query`高，查 SQL、排名和候选上限。若`live_read`高，查文件大小、存储延迟和重复读取。若`result.build`高，查是否生成了过多候选、章节或审计字段。

完整检索代码为`obsidian.retrieval.validate`、`obsidian.retrieval.index.status`、`obsidian.retrieval.index.refresh.enumerate`、`obsidian.retrieval.index.refresh.classify`、`obsidian.retrieval.index.refresh.parse`、`obsidian.retrieval.index.refresh.commit`、`obsidian.retrieval.query`、`obsidian.retrieval.note.resolve`、`obsidian.retrieval.note.live_read`、`obsidian.retrieval.note.link_metadata`、`obsidian.retrieval.result.build`和`obsidian.retrieval.result.build.candidates`。

## 编辑、批量和资源 Harness

`obsidian.edit.snapshot`读取文件并计算写入保护信息；`transform`只计算新文本；`resource.preflight.*`依次比较引用、调用 Harness 预检并解析受影响资源 ID；`atomic_write`执行受哈希保护的替换；`verify`重新读取并核对结果；`resource.sync.dry_run`和`resource.sync.write`分别记录缓存同步的预览和正式写入。

批量编辑使用`manifest`、`preflight`、`assertions`、`prepare`、`write`、`resource.sync`和`rollback`。若`rollback`出现，它表示写入阶段发生错误后进行了恢复；不能把这段时间解释为正常编辑成本。资源阶段高时应先查 Harness 进程启动次数、注册表查询和逐文件同步；写入阶段高时查文件数量、杀毒扫描、存储和原子替换。

完整编辑代码为`obsidian.edit.snapshot`、`obsidian.edit.transform`、`obsidian.edit.resource.preflight.scan_compare`、`obsidian.edit.resource.preflight.harness_preview`、`obsidian.edit.resource.preflight.resolve_ids`、`obsidian.edit.atomic_write`、`obsidian.edit.verify`、`obsidian.edit.resource.sync.dry_run`和`obsidian.edit.resource.sync.write`。完整批量代码为`obsidian.batch.manifest`、`obsidian.batch.preflight`、`obsidian.batch.assertions`、`obsidian.batch.prepare`、`obsidian.batch.write`、`obsidian.batch.resource.sync`和`obsidian.batch.rollback`。

## Impact、lint、Git 和统计

`obsidian.impact.enumerate`枚举文件，`build_index`建立标题索引，`scan`扫描链接和关键词。`obsidian.lint.acquire_scope`确定全文件或 Git diff 范围，`scan`执行文本规则，`semantic_sentinels`比较事实、数字、公式和链接哨兵。`obsidian.git.*`只做窄范围只读检查，不提交也不 push。`obsidian.stats.enumerate`枚举统计范围，`scan`读取并聚合，`index_status`补充索引状态。

枚举阶段持续偏高，通常应查目录范围和文件数量。扫描阶段持续偏高，查是否多次读取同一文件、正则是否重复编译、能否复用一次枚举结果。Git 阶段高时查 pathspec 和仓库状态；不要据此放宽提交或 push 权限。

其余完整代码为`obsidian.impact.enumerate`、`obsidian.impact.build_index`、`obsidian.impact.scan`、`obsidian.lint.acquire_scope`、`obsidian.lint.scan`、`obsidian.lint.semantic_sentinels`、`obsidian.git.precheck`、`obsidian.git.diff`、`obsidian.git.diff_check`、`obsidian.stats.enumerate`、`obsidian.stats.scan`和`obsidian.stats.index_status`。

## 字段和隐私

阶段业务字段只允许`mode`、`operation`、`write`、`file_count`、`candidate_count`、`byte_count`、`harness_call_count`、`result_count`、`change_count`、`warning_count`和`error_count`。它们用于区分任务类别和工作量，不能证明笔记内容、查询含义或结果质量。

阶段记录不得包含路径、笔记标题、查询词、正文、片段、标题名、diff、命令、异常文本、资源 ID、文件哈希原值或工具输出。`script_sha256`由 Observer 对脚本文件计算，只用于区分脚本版本；脚本不能把业务文件哈希放入阶段字段。

父阶段的`inclusive_ms`包含子阶段，`exclusive_ms`扣除已登记子阶段的区间并集。并行时各阶段时长之和可能大于墙钟时间，分析应使用`observed_wall_ms`和覆盖率判断实际覆盖，不能把阶段时长直接相加后当成总耗时。


`title`与`note_title`均为禁止字段；它们不得出现在阶段数据中。


## 通用脚本入口

`obsidian-vault-notes.script.run`记录通过本地失败放行包装器启动的生产脚本完整子进程墙钟时间；详细叶阶段仍用于定位内部耗时。
