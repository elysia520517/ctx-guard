<div align="center">

# ctx-guard · 上下文卫士

**让 Claude Code 的长会话不再"爆上下文"。**

长粘贴自动切块 → 超大读取自动拦截 → 爆了自动重启接着干，全程零手动。

[English](README.en.md) · **中文**

</div>

---

## 这是什么

`ctx-guard` 是一个 [Claude Code](https://claude.com/claude-code) 技能（skill），再加一组
hook 和一个守护进程，专治"聊着聊着上下文就满了"。它做三件事：

1. **超大输入在进入上下文之前就被切碎。**
   你粘一大段文本 → 它先写到磁盘、切成小块、分给子代理逐块处理。**只有子代理的结论进入
   你的上下文，原文永远不进。**
2. **硬拦最经典的"上下文杀手"。**
   一个 `PreToolUse` hook 会拒绝整读大于 200 KB 的文本文件、或用一个裸 `cat` 把大文件
   全倒出来，并把"改用 `offset`/`limit` / `| head`"提示回传给模型让它重试。
3. **满了自动重启，接着干。**
   重启时 `SessionStart` hook 自动把一份精简交接单重新注入；可选的后台进程 `watchdog.py`
   盯着会话，一旦它**正在忙且快满**，就自动写交接单并起一个新的 `claude --bg` 会话继续。
   **全程不用你动手。**

> **与 Anthropic 无隶属关系。** 针对 Claude Code `2.1.295` 开发。

---

## 到底什么在吃上下文（实测，不是猜的）

从一个 85 MB 的真实会话记录里统计的 token 占比：

| 来源 | tokens | 占比 |
|---|---:|---:|
| 模型 **思考（thinking）** | 1.10 M | **37%** |
| 用户消息（含 77 条 auto-compact 摘要） | 870 k | 30% |
| 工具结果 · 文本（1451 次调用） | 839 k | 28% |
| 工具结果 · 图片（92 张） | 118 k | 4% |

两个反直觉的结论：

- **图片不是主要矛盾。** 18 MB 的 base64 只值约 118 k token（每张图封顶约 1600 token，
  代价 ≈ `宽×高/750`）。把截图缩得更小，帮助很有限。
- **auto-compact 摘要是隐藏的黑洞。** 那个会话被自动压缩了 **77 次**，每次约 34 KB 摘要
  都会进入历史。真正的解药是**主动 `/clear`**，而不是等它自动压。

所以：这个工具覆盖的是"一个技能**够得着**"的那部分（输入 + 工具输出，约三分之一）；
思考和压缩摘要要靠"早重启、勤清理"。

---

## 目录结构

```
skills/ctx-guard/
├── SKILL.md              # 技能本体：触发词 + 规则
├── watchdog.cmd          # Windows 启动器（自动重启守护进程）
└── scripts/
    ├── scan.py           # 会话"重量"体检（只读）
    ├── digest.py         # 巨大会话 → ~8 KB 交接单（只读）
    ├── split.py          # 把超长文本/文件切块，供分段执行
    ├── resume.py         # 写 RESUME_NEXT.md，交给下一个会话
    ├── gate.py           # PreToolUse：拦截超大读取 / 裸 cat
    ├── hook_guard.py     # PreToolUse：更温和的"只提醒不拦"版本
    ├── paste_split.py    # UserPromptSubmit：长粘贴 → 落盘 + 切块 + 规则
    ├── session_resume.py # SessionStart：重启后自动注入交接单
    ├── statusline.py     # statusLine：显示 `ctx NN%`，并写用量给 watchdog
    └── watchdog.py       # 会话忙且快满时，自动重启接力
```

所有脚本对**你的代码只读**；它们只在 `~/.claude/` 和当前目录的 `.ctxguard-paste/` 下写文件。

---

## 安装

把仓库克隆到任意位置，然后把技能目录拷进（或软链到）你的 Claude Code skills 目录：

```bash
# macOS / Linux
cp -r skills/ctx-guard ~/.claude/skills/

# Windows (PowerShell)
Copy-Item -Recurse skills\ctx-guard "$env:USERPROFILE\.claude\skills\"
```

然后在 `~/.claude/settings.json` 里挂上这些 hook（**合并**，不要覆盖掉你已有的 `env` 等配置）：

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Read|Bash",
        "hooks": [{ "type": "command",
                    "command": "python -I ~/.claude/skills/ctx-guard/scripts/gate.py" }] }
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command",
                    "command": "python -I ~/.claude/skills/ctx-guard/scripts/paste_split.py" }] }
    ],
    "SessionStart": [
      { "hooks": [{ "type": "command",
                    "command": "python -I ~/.claude/skills/ctx-guard/scripts/session_resume.py" }] }
    ]
  },
  "statusLine": {
    "type": "command",
    "command": "python -I ~/.claude/skills/ctx-guard/scripts/statusline.py"
  }
}
```

Windows 上请用绝对路径（例如 `python -I C:/Users/you/.claude/skills/.../gate.py`）。
**重启会话**后 hook 才会生效。

> 一律用 `python -I` 调用脚本 —— 它会把脚本和当前目录隔离，避免当前目录里一个同名
> 的 `json.py` / `sitecustomize.py` 被意外加载而劫持脚本。

---

## 用法

```bash
S=~/.claude/skills/ctx-guard/scripts

# 体检：这个会话到底是什么在占地方？
python -I $S/scan.py --top 12
python -I $S/scan.py --all                # 本项目所有会话按体积排名

# 把超长文本切块，供分段执行
python -I $S/split.py big.txt --max-kb 40
python -I $S/split.py big.txt --chunks 8 --overlap-lines 3

# 生成交接单，然后重开新会话说一句"继续"
python -I $S/resume.py
```

### 全自动重启

```bash
python -I $S/watchdog.py --dir "/path/to/your/project" --threshold 82 --poll 20 --max-restarts 10
# Windows 也可以:  watchdog.cmd "C:\你的项目"
```

守护进程**只在会话真的出状况时**才重启：

- 会话是 `busy` **且** 用量 ≥ 阈值；或
- 会话**在忙的过程中结束了**（崩溃 / 被中途杀掉）。

它**不会**去重启一个闲置但占用很高的会话（那只是它在等你）—— 从而避免毫无意义的"复活"。

**前提：** 目标目录必须已被 Claude Code **信任**（需要在该目录里交互式跑一次 `claude` 并接受
信任提示），否则 `claude --bg` 会拒绝启动。守护进程会如实报告这一点并干净退出，而不是空转。

`--max-restarts` 就是**安全绳**（启动器里默认 10），用来给自动重启封顶、防止开支失控。

**脚本必须在 `python -I` 下运行，并且每个脚本都会把 stdout/stderr 重设为 UTF-8**
（Windows 控制台默认 GBK，会让这些脚本输出里的中文变成乱码）。

---

## 调参（在 settings.json 的 `env` 里设置）

| 变量 | 默认 | 含义 |
|---|---|---|
| `CTXGUARD_READ_KB` | `200` | 超过这个大小的文本文件，禁止整读 |
| `CTXGUARD_BLOCK` | `on` | 设为 `off` → `gate.py` 退化为"只提醒不拦" |
| `CTXGUARD_PASTE_KB` | `12` | 触发自动切块的粘贴大小 |
| `CTXGUARD_CHUNK_KB` | `40` | `split.py` 的切块大小 |

如果你不想被打断，可以把 `gate.py` 换成更温和的 `hook_guard.py`（永不拦截，永远 `exit 0`）。

---

## 说明与局限

- 在 **Windows 11** + Claude Code `2.1.295` 上开发并测试；hook 里的路径是 Windows 风格。
  Linux / macOS 需要自行调整路径，并把 `watchdog.cmd` 换成对应 shell 启动脚本。
- `gate.py` 的硬拦截依赖 `PreToolUse` 退出码为 2 的官方语义（stderr 回传给模型）。该语义
  由文档/契约确认；此机器上未复现完整端到端拦截，详见代码注释。
- 只依赖 Python 3 标准库，无第三方依赖。

## 许可

MIT —— 见 [LICENSE](LICENSE)。
