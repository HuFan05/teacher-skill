# 维护手册

## 1. 什么会自动维护，什么需要你决定

| 自动完成，不需要任何人记得 | 需要你一次封闭决定 |
| --- | --- |
| 每个回答后抽取归档候选（结论、来源、失败的检查、未解问题），按内容去重、按证据打分 | 候选是否进入正式研究库（`archive`：只归档已核验 / 全部 / 都不要） |
| 结论的依赖被修改后，效果自动变为 `needs_review` | 改变一个结论的**含义**（`engine -- maintain`，必须写明理由） |
| 已索引文件被修改或删除时，读取时如实标注 `changed_since_index` / `stale` | 重新索引一个目录（`index 目录`，再 `--apply` 计划哈希） |
| 核心认知（目标、已核验主干、证据边界、未解问题、检索触发词……）每次从库里重算，按预算选阅读层 | 绑定研究目标（`engine -- intake-begin` 到 `intake-commit`） |
| 同类问题反复出现时主动提议整理或升格 | 是否接受提议 |

知识库的价值判断——稀缺、可信、值得复用——始终由你做；Teacher 把它压成一次选择，而不是替你决定。

## 2. 日常检查

```sh
cd <技能目录>/teacher/scripts
python3 teacher.py status             # 两个 head、库里各类条目数量、索引覆盖、回答数
python3 teacher.py cognition          # 每次交给模型的核心认知及其阅读层收据
python3 teacher.py archive            # 待归档候选
python3 teacher.py engine -- maintenance            # 依赖漂移、过期锚点
python3 teacher.py engine -- chain --json           # 校验两条哈希链
```

## 3. 索引

- `teacher.py index 目录` 只做预演：列出文件数、PDF 数、PDF 文字提取是否可用和 `plan_sha256`。
- `teacher.py index 目录 --apply <plan_sha256>` 按那份计划建立索引；目录在两次之间变化时会记为 `changed_since_plan`。
- PDF 需要本机有 `pdftotext`；没有文字层的扫描件记为 `no_text_layer`，不会被当作“没有内容”。
- 大改之后重跑一次即可；旧片段会被替换。

## 4. 研究资产的层次

| 层 | 内容 | 在哪里 |
| --- | --- | --- |
| 对话记录 | 每次的需求框定、回答、证据片段、指标 | Teacher 研究库（`execution` head） |
| 待归档候选 | 自动抽取、尚未决定的条目 | 同上 |
| 正式研究库 | 你决定归档的结论、来源、失败记录、未解问题 | 同上（`authority` head），下一次提问优先检索 |
| 持续研究项目 | 带证据、评审、地图和交接包的研究记录 | `cs-ai-research-solve` 技能的项目目录 |
| 笔记 | 你自己写的、经过加工的理解 | Obsidian 笔记库（`obsidian-vault-notes` 技能负责读写） |

核心认知每次从库里重算，按预算压缩后交给模型，并保留十个受保护字段。

## 5. 问题诊断

| 现象 | 处理 |
| --- | --- |
| `check-answer` 退出码 3 | 看返回的封闭风险码；什么都没写，两个 head 都没动 |
| 引文显示 `declared` 而不是 `verified` | `setup --show` 看 `read_roots` 与 `network`；补 `--read-root` 或开启网络 |
| 从不引用你的笔记 | `status` 看 `retrieval.roots`；没有就 `index` 并 `--apply` |
| 某次失败想知道原因 | 研究库的事件里只记录封闭的风险码与字段路径，不记录被拒绝的内容 |
| 一个写入被拒绝 | 看返回的封闭码；什么都没写，两个 head 都没动 |
| 结论显示 `needs_review` | 依赖动了，运行 `engine -- maintenance` |
| 完成评估被拒 | 评估已过期，重新 `assess` |

## 6. 更新与校验

- 包的完整性：在包根目录运行 `python3 tools/checksums.py`（只校验）。本地改动技能后如需重新发放，运行 `python3 tools/checksums.py --write` 重算清单。
- 已安装的技能：`python3 install.py --doctor --agent <codex|claude|generic> …`；文件被改动时返回 51。
- 替换为新版本：用原来的安装参数加 `--replace`；失败时所有技能和受管配置一起回滚。
- 每个技能自带测试与术语校验，例如 `python3 -m unittest discover -s payload/teacher/scripts/tests -p "test_*.py"`、`python3 payload/<技能>/scripts/validate_terminology.py --skill-root payload/<技能> --json`。

修改规则时的顺序：先改程序规则与测试，确认测试通过，再改对应技能的 `SKILL.md` 与 `references/`，最后重算校验和。`SKILL.md` 只保留流程判断、硬约束、脚本入口与验收标准；细节放在 `references/`；能由程序检查的，交给脚本和测试。

## 7. 运行观测（可选）

各技能开头的 “Skill Run Observation” 只在环境变量 `SKILL_OBSERVER_CLI` 指向一个观测程序时才生效，只记录阶段与耗时，不记录提示词、正文、路径或密钥；观测失败不会影响任务。不设置这个变量时它什么也不做。

## 8. 备份、卸载与清理

- 研究库和配置都在用户目录（见 `START_HERE.md`）；备份就是复制这些目录。
- `python3 install.py --uninstall … --yes` 移除技能，保留配置、研究库与备份。
- `--purge-local-state --yes` 才会删除本地配置、索引与安装备份；研究库文件 `teacher.sqlite3` 仍保留，删除它需要你自己动手。
