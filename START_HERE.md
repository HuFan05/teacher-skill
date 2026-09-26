# Teacher 1.0.0

Teacher 是面向计算机科学与人工智能学习、研究的本地研究型老师。你不必先把问题想清楚：它先弄清你真正想问什么（必要时只问你一个选择题），再查你自己的笔记、研究资料库、论文和网络，然后给出简短的回答，每一条都写明依据什么、证据有多强、不能推出什么、还有什么不知道；值得保留的结论和来源会自动整理成候选，你只需决定一次要不要归档。

需要 Python 3.10 或更新版本，不需要安装任何第三方包。

## 它以 Skill 的形式工作

把六个技能装进 Codex、Claude Code 或其他支持 Skill 的 agent：

```sh
python3 install.py --agent claude --yes      # Claude Code
python3 install.py --agent codex --yes       # Codex
```

macOS/Linux 在本目录运行 `sh install.sh`，Windows 双击 `INSTALL_WINDOWS.cmd`；也可以
`python3 install.py --agent generic --target-root <你的 agent 技能目录> --yes`。
其他 agent 请把 `AGENT_INSTALL.md` 交给它。目标目录已有同名技能时安装会停下（退出码 21），
确认后再加 `--replace`，旧版本会先备份。

装好后**新开一个 agent 会话**，说 `$teacher 你的问题`。agent 用自己的工具调研；
Teacher 负责两件事——框定问题时判断要不要问你、问什么，以及答案能不能交付。
这两件事是同一套确定性规则，不依赖模型。

安装不需要联网，也不会自动索引你的资料。检查安装：`python3 install.py --doctor --agent claude`；
卸载：`python3 install.py --uninstall --agent claude --yes`（默认保留配置、备份和研究库）。

配置与研究库都放在用户目录，不在本包里：macOS 为 `~/Library/Application Support/teacher/`，
Linux 为 `~/.config/teacher/` 与 `~/.local/state/teacher/`，Windows 为 `%APPDATA%\teacher\` 与 `%LOCALAPPDATA%\teacher\`。

日常用法见 `docs/USAGE.md`；资产维护、诊断和更新见 `docs/MAINTENANCE.md`。
