# Teacher

面向计算机科学与人工智能学习、研究的本地研究型老师：先弄清你真正想问什么，再查你的笔记、研究资料库、论文和网络，然后简短作答——每条都写明依据、证据强度、不能推出什么、还有什么不知道；值得保留的结论自动整理成候选，由你一次决定是否归档。

它以 Skill 的形式装进 Codex / Claude Code 等支持 Skill 的 agent：**agent 用自己的工具调研，框定和答案都经过同一套确定性检查**，检查通过的文字由 Teacher 渲染，agent 原样交付给你。

需要 Python 3.10+，不依赖任何第三方包。

## 安装

```sh
python3 install.py --agent codex --yes      # Codex
python3 install.py --agent claude --yes     # Claude Code
python3 install.py --agent generic --target-root <你的 agent 技能目录> --yes
```

macOS/Linux 也可以 `sh install.sh`，Windows 双击 `INSTALL_WINDOWS.cmd`。其他 agent 请把 `AGENT_INSTALL.md` 交给它。
目标目录已有同名技能时安装会停下（退出码 21），确认后加 `--replace`，旧版本会先备份。

安装不需要联网，也不会自动索引你的资料。检查安装：`python3 install.py --doctor --agent claude`；
卸载：`python3 install.py --uninstall --agent claude --yes`（默认保留配置与研究库）。

## 日常用法

装好后新开一个 agent 会话，说 `$teacher 你的问题`。你不必先把问题说清楚：需要时 Teacher 只问你**一次**选择题，
答完由 agent 调研，答案过检查后再交到你手上。想让检查更严格时，可以让 agent 再索引你的笔记目录：

```sh
python3 <技能目录>/teacher/scripts/teacher.py index ~/笔记目录   # 先看会索引多少文件
python3 <技能目录>/teacher/scripts/teacher.py index ~/笔记目录 --apply <plan_sha256>
```

配置与研究库都放在用户目录，不在本包里：macOS 为 `~/Library/Application Support/teacher/`，
Linux 为 `~/.config/teacher/` 与 `~/.local/state/teacher/`，Windows 为 `%APPDATA%\teacher\` 与 `%LOCALAPPDATA%\teacher\`。

- 入门：[START_HERE.md](START_HERE.md)
- 使用说明：[docs/USAGE.md](docs/USAGE.md)
- 维护手册：[docs/MAINTENANCE.md](docs/MAINTENANCE.md)
- 给 agent 的安装手册：[AGENT_INSTALL.md](AGENT_INSTALL.md)
