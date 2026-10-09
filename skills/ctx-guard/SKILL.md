---
name: ctx-guard
description: 防止上下文爆仓（爆 context / 爆 token）。当用户说"爆上下文/上下文太长/要不要 /clear/又满了/接不上了/上下文还有多少"，或一次会话体积超过 ~10 MB，或要粘/读一大段文本、大日志、大批文件（>~12 KB）、要连续做很多条独立任务时，用本技能。含：session 体检(scan.py)、巨会话压成交接单(digest.py)、长文本切块分段执行(split.py)、已挂载的自动护栏(gate/paste_split/statusline)。内部按 harness 适配层组织（Claude Code 默认、最完整；Codex / OpenAI SDK 有其适配器骨架），换工具不用改脚本。
---

# ctx-guard —— 防止上下文爆仓

核心事实：撑爆上下文的是**工具输出**、**模型思考(thinking)** 和 **auto-compact
摘要**，不是代码本身。

## 0. 用户粘了一大段文本 / 给了个大文件 → 切块，分段执行

这是"输入过多"的主动解法。**别把原文整段读进上下文。**

### 已经自动做了一半（UserPromptSubmit hook `paste_split.py`，已挂 settings.json）
用户一次粘贴 > 12 KB 时，hook 会自动：
1. 把原文写到 `<cwd>/.ctxguard-paste/paste-<时间>.txt`（**原文不进主上下文**）；
2. 切成 ≤40 KB/块到 `…txt.parts/`；
3. 给模型一段 `additionalContext`："已落盘，别再整段读，按块派子代理"。

### 模型该照做的事（分段执行）
收到那段提示后**不要**再去 Read 那些块来"看看内容"，而是：
```
每块 → Agent(prompt="任务: <一句话目标>。只读 <part-NNNN.txt>。
        把结论压成 ≤10 条要点返回；不要贴原文，不要读别的块。")
最后 → 只汇总各块结论
```
原文与各块全程留在磁盘，主上下文只累积"结论"。

### 手动切块
```bash
python -I .claude/skills/ctx-guard/scripts/split.py big.txt                 # ≤40KB/块
python -I .claude/skills/ctx-guard/scripts/split.py big.txt --chunks 8      # 指定块数
python -I .claude/skills/ctx-guard/scripts/split.py big.txt --overlap-lines 3  # 每块带上一块末尾几行
python -I .claude/skills/ctx-guard/scripts/split.py - < big.txt             # 从 stdin
```
产出 `big.txt.parts/part-0001.txt …` + `MANIFEST.json`（每块行号/字节/估token）
+ 屏幕上的分段执行模板。阈值/块大小可用 `CTXGUARD_PASTE_KB`、`CTXGUARD_CHUNK_KB` 调。

## 1. 先量：30 秒体检

```bash
python -I .claude/skills/ctx-guard/scripts/scan.py --top 12
```
看**哪个类别占 token 最多**、**最重的工具输出来自哪个工具/哪个文件**——那就是元凶。
其他：`--all`(本项目会话按体积) · `scan.py <路径.jsonl>` · `--project <项目目录名>`
注意：会话文件大小 ≠ 当前上下文（含早被压缩丢掉的累积）；用它衡量"反复灌进来的东西"。

### 本机实测结论（2026-10-09，扫一个 85 MB 会话）

| 消耗源 | 估token | 占比 |
|---|---|---|
| thinking(模型思考) | 1.10 M | **37%** |
| user_prompt（其中 auto-compact 摘要 77 条） | 870 k | 30% |
| tool_result(文本) 1451 条 | 839 k | 28% |
| tool_result(图片) 92 张 | 118 k | 4% |
| 工具调用+正文 | 17 k | 1% |

两个反直觉点：**① 图片不是主要矛盾**（18 MB base64 只值 ~118 k token，单图封顶 1600）；
**② auto-compact 摘要本身很重**（该会话压了 77 次，每次 ~34 KB 进历史——"越压越满"）。
所以解药是**主动 /clear**，别等它自动压。

## 2. 爆了/快爆了：重启，然后接着把活干完

目标：**不用重新解释任务**，新会话自动接上，从"上次停在哪"继续。

```bash
python -I .claude/skills/ctx-guard/scripts/resume.py          # 交接当前项目最新会话
python -I .claude/skills/ctx-guard/scripts/resume.py --prev   # 已重开、想接更早那个会话
python -I .claude/skills/ctx-guard/scripts/digest.py --stdout # 只打屏幕不写文件
```
`resume.py` 写出 `<项目目录>/RESUME_NEXT.md`（~8 KB），只留：用户说过什么 ·
改过哪些文件(已落盘，别重做) · 跑过哪些命令 · 子代理任务 · **上次停在哪** ——
工具输出全丢。

**然后直接开新会话**（`/clear` 或同目录重开 `claude`）。新会话启动时
`SessionStart` hook (`session_resume.py`) 会**自动**把 RESUME_NEXT.md 注入上下文
并把它消费掉（改名 `.consumed`，不重复注入）。你只要说一句 **"继续"**。

> 若没自动接上（比如换了目录/过期 >24h），手动把
> `RESUME_NEXT.md` 内容贴进去，附一句"据此继续未完成的任务"。

**重启的 3 条纪律**（交接单里已写明，照着做）：
1. **已落盘的改动别重做** —— 交接单的"改过的文件"就是既有事实，先确认再动手。
2. 只从"上次停在哪"续，不要重新论证已定的事。
3. 续的活如果是"读大文件/长文本"，先 `split.py` 切块再派子代理，别把上轮的坑再踩一遍。

间隙性主动重启（没爆也重启）通常比压缩更省：**任务切换时就重启**，别等 80%。

## 2b. 全自动重启（零人工，靠独立 watchdog 进程）

上面那套是"半自动"（要你开新会话）。要**彻底免手动**，跑独立进程 `watchdog.py`：

```bash
python -I .claude/skills/ctx-guard/scripts/watchdog.py --dir "<你的项目目录>" \
        --threshold 82 --poll 20            # 也可双击 watchdog.cmd
```

它每 20s 检查一次，**只在会话确实在忙、且快满时**才自动交接 + 起新背景会话
（`claude --bg`）接着干：
- A) 会话正 **busy** 且 ctx ≥ threshold → 自动接力（边干边满）
- B) 会话已结束、且**结束前在忙** → 自动续（干着干着爆了/被杀）
- **不触发**：会话 idle 时高占用（任务做完了在等你）、idle 后正常收工 —— 防误"复活"。

配套：`statusline.py` 会把每个会话的占用写到
`~/.claude/ctx-guard-sessions/<session_id>.json`（按会话分档，多开不串），
watchdog 用它拿用量，用 `claude agents --json` 判断会话死活/busy/idle。

> **前提**：目标目录必须**已被信任** —— 先在那个目录交互式跑一次 `claude` 并接受
> 信任提示。否则 `--bg` 拒绝启动，watchdog 会报错停下（不空转、不刷屏）。
> **止损**：默认不限次（按你要求）；建议至少加 `--max-restarts 5`，防开支失控。
> 运行日志：`<dir>/.ctxguard-watchdog.log`。

## 3. 已挂载的自动护栏（`~/.claude/settings.json`，重开会话生效）

| 事件 | 脚本 | 行为 | 非 Claude 工具下 |
|---|---|---|---|
| `PreToolUse` (Read\|Bash) | `gate.py` | **拦截**：整读 >200 KB 文本 / 无节流的裸 `cat` 大文件 → exit 2，把"改用 offset/limit | head"喂回模型重试 | 能拦则拦（退出码由适配器定）；拦不了自动退化成"只注入提醒" |
| `UserPromptSubmit` | `paste_split.py` | 长粘贴自动落盘+切块+给分段指令 | 同上，注入方式由适配器决定 |
| `SessionStart` | `session_resume.py` | 重启后**自动注入**上一会话的 `RESUME_NEXT.md` 并消费掉（"爆了重启接任务"的自动一半） | 有对应事件就注入，没有就手动贴 |
| `statusLine` | `statusline.py` | 显示 `ctx NN%`，并把用量写 `~/.claude/ctx-guard-usage.json` 供 gate 参考 | 该工具没有状态栏 → 只是少一行显示，护栏照常 |

**gate 有意放行的**（不误杀）：图片(`.png/.jpg/…`，panel 封顶)、`.jsonl/.log`（交给提醒版）、
小文件、已带 `head`/`|`/`>`/offset/limit 的调用。默认阈值 200 KB。
调参（写进 settings.json 的 `env`）：`CTXGUARD_READ_KB`、`CTXGUARD_BLOCK`(off=只提醒不拦)、
`CTXGUARD_PASTE_KB`、`CTXGUARD_CHUNK_KB`。
关掉拦截：把 `CTXGUARD_BLOCK` 设 `off`，或删掉 `hooks`/`statusLine` 键。

**换工具时不用改脚本。** 读会话、判用量、拦工具、起新会话这些环境差异都在
`scripts/adapter_*.py` 里；`CTXGUARD_HARNESS=<名字>` 可以点名用哪个适配器，空则自动探测。
当前：Claude Code 完整可用；Codex / OpenAI Agents SDK 有骨架但**未在真机验证**。
写新适配器见仓库根的 `docs/adding-a-harness.md`。

`hook_guard.py` 是更轻的**纯提醒版**（exit 0，从不拦），若不想被 gate 打断可改挂它：
```json
{"hooks":{"PreToolUse":[{"matcher":"Bash|Read|Grep|Glob",
  "hooks":[{"type":"command","command":"python -I <…>/hook_guard.py"}]}]}}
```

## 4. 硬规则（干大活时逐条执行）

- **读**：先 `Grep`/`Glob` 定位，再 `Read` 带 `offset/limit`；同一文件别反复整读。
- **跑**：命令一定截断 `| head -n 60`；先 `wc -l` 再取窗口；编译/测试只留 `grep -iE 'error|warning'`。
- **搜**：让子代理(Explore)去搜，只回结论，不回文件内容。
- **贴**：一次一张；长文先 `split.py` 落盘再分段。
- **切**：每 2~3 个独立任务评估一次要不要 `/clear`（用 digest 交接）。

## 5. 一句话

**主上下文只放"结论"和"待办"，不放"原始输出"。** 原始输出要么截断，要么落盘切块给
子代理，要么交给它们看完只回一句。
