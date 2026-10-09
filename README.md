<div align="center">

# ctx-guard · 上下文卫士

**让 Claude Code 的长会话别再"爆上下文"。**

长粘贴自动切块 → 超大读取自动拦截 → 爆了自动重启接着干，全程零手动。

[English](README.en.md) · **中文**

</div>

---

## 这是个什么工具

`ctx-guard` 是一个 [Claude Code](https://claude.com/claude-code) 技能：一份技能说明，加一组
hook，再加一个看门用的后台进程。它专治一件事——聊着聊着，上下文就满了。

具体管三件事：

**一、把过大的输入挡在上下文外面。**
你扔进来一大段文本，它先落到磁盘、切成小块，再一块一块交给子代理去读。最后**只有子代理的
结论会进到你的上下文里，原文一个字都不进。**

**二、拦住那几个最经典的"上下文杀手"。**
一个 `PreToolUse` hook 会拒绝整读超过 200 KB 的文本文件，也拒绝用一句裸 `cat` 把大文件全倒
出来，同时把"改用 `offset`/`limit`，或者接个 `| head`"这句话回传给模型，让它自己重试。

**三、满了就自己重开，接着干。**
重启时，`SessionStart` hook 会把上一回留下的精简交接单自动塞回来；另外有个可选的
`watchdog.py` 在后台盯着会话，**一旦发现它正忙、又快满**，就写好交接单、起一个新的
`claude --bg` 会话接着做。**整个过程不用你动手。**

> **本项目与 Anthropic 无隶属关系。** 面向 Claude Code `2.1.295` 开发。

---

## 到底什么在吃上下文（实测，不是猜的）

数字来自我自己一个 85 MB 的会话记录，按估算的 token 占比统计：

| 来源 | tokens | 占比 |
|---|---:|---:|
| 模型 **思考（thinking）** | 1.10 M | **37%** |
| 用户消息（含 77 条 auto-compact 摘要） | 870 k | 30% |
| 工具结果 · 文本（1451 次调用） | 839 k | 28% |
| 工具结果 · 图片（92 张） | 118 k | 4% |

有两个结论挺反直觉：

- **图片根本不是大头。** 18 MB 的 base64 也只值大约 118 k token —— 每张图封顶约 1600 token，
  代价差不多是 `宽×高/750`。把截图缩得更小，帮不上什么忙。
- **auto-compact 摘要是那个看不见的洞。** 那个会话被自动压缩了 **77 次**，每次约 34 KB 的摘要
  都会留在历史里。真正的解药是**主动 `/clear`**，而不是等它自己压。

所以说，这个工具能管到的，只是"一个技能够得着"的那部分——输入，加上工具输出，大概三分之一。
剩下的思考和压缩摘要，只能靠"早重启、勤清理"。

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
    ├── watchdog.py       # 会话忙且快满时，自动重启接力
    │
    ├── harness.py        # 探测当前跑在哪个工具下 + hook 输入大门
    ├── adapter_base.py   # 适配器基类与"归一化事件流"约定
    ├── adapter_claude.py # Claude Code 适配器（默认、最完整）
    ├── adapter_codex.py  # Codex CLI 适配器（骨架，未实测）
    ├── adapter_openai.py # OpenAI Agents SDK 适配器（鸭子类型接入）
    ├── config.py         # 统一配置读取（env + 可选 json）
    ├── paths.py          # 唯一的项目目录 / 会话查找实现
    └── tokens.py         # 唯一的 token 估算实现
```

这些脚本对你的代码一律只读，只会在 `~/.claude/` 和当前目录的 `.ctxguard-paste/` 下写文件。

---

## 安装

把仓库克隆到任意位置，再把技能目录拷进（或者软链到）你的 Claude Code skills 目录：

```bash
# macOS / Linux
cp -r skills/ctx-guard ~/.claude/skills/

# Windows (PowerShell)
Copy-Item -Recurse skills\ctx-guard "$env:USERPROFILE\.claude\skills\"
```

然后把这些 hook 挂到 `~/.claude/settings.json` 里。**注意是合并**——别把你原来就有的 `env` 之类
的配置覆盖掉：

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

Windows 上路径要写绝对路径（比如 `python -I C:/Users/you/.claude/skills/.../gate.py`）。
**会话要重启，hook 才会生效。**

> 脚本一律用 `python -I` 调用。它会把脚本和当前目录隔开，免得当前目录里正好有个同名的
> `json.py` / `sitecustomize.py` 被误加载、把你的脚本劫持了。

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

看门进程只在会话真的出状况时才动手：

- 会话是 `busy`，**并且**用量到了阈值；或者
- 会话**在忙的过程中没了**（崩了、被中途杀掉）。

它**不会**去重启一个闲着但占用很高的会话——那只是它在等你答话——这样能避开毫无意义的"复活"。

**前提：** 目标目录得先被 Claude Code **信任**（在该目录里交互式跑一次 `claude`，把信任提示
点了）。不然 `claude --bg` 会拒绝启动。碰上这种情况，看门进程会如实报出来然后干净地退出，
而不是在那儿空转。

`--max-restarts` 就是那根**安全绳**（启动器里默认 10），给自动重启封个顶，免得开销失控。

**脚本必须在 `python -I` 下跑，而且每个脚本都会把 stdout/stderr 重设成 UTF-8**（Windows 控制台
默认 GBK，不重设的话这些脚本里的中文会变成乱码）。

---

## 调参（在 settings.json 的 `env` 里设置）

| 变量 | 默认 | 含义 |
|---|---|---|
| `CTXGUARD_READ_KB` | `200` | 超过这个大小的文本文件，禁止整读 |
| `CTXGUARD_BLOCK` | `on` | 设为 `off` → `gate.py` 退化为"只提醒不拦" |
| `CTXGUARD_PASTE_KB` | `12` | 触发自动切块的粘贴大小 |
| `CTXGUARD_CHUNK_KB` | `40` | `split.py` 的切块大小 |
| `CTXGUARD_HARNESS` | 空 | 点名用哪个适配器；空 = 自动探测 |
| `CTXGUARD_ADAPTERS` | 空 | 限制自动探测的候选集，如 `claude,codex` |
| `CTXGUARD_STATE_DIR` | 空 | 运行时状态根目录；空 = 该工具的默认位置 |

也可以把它们写进一个扁平 JSON（`~/.claude/ctxguard.json`，键名省掉 `CTXGUARD_` 前缀），
整份文件不存在也不影响，纯环境变量照样跑。

不想被打断的话，把 `gate.py` 换成更温和的 `hook_guard.py` 就行——它从不拦截，永远 `exit 0`。

---

## 支持哪些工具

"换个工具就得把整套重写一遍"是这个项目一开始最想避免的结局。所以读会话、判用量、拦工具、
起新会话这些**环境事实**全部抽进了一层适配器，脚本本身只跟适配器说话。

| 能力 | Claude Code | Codex CLI | OpenAI Agents SDK |
|---|---|---|---|
| 识别自身（`detect`） | ✅ | ✅ 环境指纹 | ✅ |
| 会话记录 → 归一化事件流 | ✅ 已实测 | ⛔ 骨架 | ⛔ |
| 读上下文占用 | ✅ statusline 落盘 | ⛔ | ⚠️ 只统计单次调用 |
| 判会话 busy/idle | ✅ `agents --json`，带文件兜底 | ⛔ | ⛔ |
| 拦截工具调用 | ✅ `exit 2` | ⛔ | ✅ 抛错 |
| 注入上下文 | ✅ `hookSpecificOutput` | ⛔ | ✅ |
| 起新会话接力 | ✅ `claude --bg` | ⛔ | ⛔ |

⛔ 不是"永远不行"，是**眼下这条没实现、也没验证过**——缺了它只是对应功能降级（只提醒、
或让你手动开新会话），不会让别的功能跟着一起坏。这就是"能力可选"的意思。

> **诚实交代：** `adapter_codex.py` 和 `adapter_openai.py` 都**没有在真机上跑通过**。
> 前者的目录规则是从 Codex 的公开源码里读出来的，后者靠鸭子类型反射 SDK 的 hooks 对象
> （那个 SDK 的 hook 方法名正在改，所以故意不 import 具体名字）。文件头都写了"未实测"和
> 待办清单。想接新工具，见 [`docs/adding-a-harness.md`](docs/adding-a-harness.md)。

### hook 不会被某个版本改名搞死

4 个 hook 脚本都不再直接 `json.load(sys.stdin)`，而是走一道统一的输入大门：读得到"认识的
字段"才处理，否则回一个空答复就退出。所以**事件改名、字段改名、或者脚本被别的工具当模块
import（根本没有 stdin）**，都不会让它崩掉或卡死，最多是安静地什么都不做。

---

## 测试

不需要装任何东西（只用标准库），也不需要网络：

```bash
python -I tests/run_tests.py          # 42 项：token 估算 / 目录规则 / 适配器 / 四个 hook 端到端
python -I tests/abcompare.py <旧目录> <新目录> <载荷目录>   # 逐字节对照新旧脚本的输出
```

`abcompare.py` 是这套改动的安全网：拿同一批真实 hook 载荷分别喂给新旧两版脚本，
把 `(退出码, stdout, stderr)` 三元组逐一对比。改动的验收标准是**差异数为 0**。

---

## 说明与局限

- 在 **Windows 11** + Claude Code `2.1.295` 上开发和测试，hook 里的路径是 Windows 风格。
  Linux / macOS 要自己调路径，`watchdog.cmd` 也换成对应 shell 的启动脚本。
- `gate.py` 的硬拦截靠的是 `PreToolUse` 退出码 2 的官方语义（stderr 回传给模型）。这个语义有
  文档和契约背书；但在这台机器上没能复现完整的端到端拦截，细节写在代码注释里。
- 只依赖 Python 3 标准库，没有任何第三方依赖。（适配器里对 OpenAI SDK 的引用是鸭子类型的，
  装了才用，没装不影响。）

## 许可

MIT —— 见 [LICENSE](LICENSE)。
