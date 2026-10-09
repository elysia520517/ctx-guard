# 更新日志

## v0.2.0 —— 版本兼容 + 多 harness 适配层

这一版没有加新功能，改的是**底子**：把"写死在脚本里的 Claude Code 细节"抽成一层可探测、
可降级、可扩展的适配器。Claude Code 侧的行为**逐字节不变**（用 24 组真实 hook 载荷做过
新旧脚本的 A/B 对照，差异数为 0）。

### 新增

- **适配层**：`harness.py`（探测 + 注册表 + hook 输入大门）、`adapter_base.py`（能力接口）、
  `adapter_claude.py` / `adapter_codex.py` / `adapter_openai.py`。
  脚本只通过 `H.<能力>()` 说话，**换工具不用改脚本**；能力缺失一律降级，不整体失效。
- **`paths.py`**：项目目录 slug 规则原本有 5 份逐字符相同的副本，现在只有一处。
- **`tokens.py`**：token 估算常数与函数原本有 3 份副本，现在只有一处。
- **`config.py`**：统一配置读取，优先级 `环境变量 > ~/.claude/ctxguard.json > 默认值`；
  新增 `CTXGUARD_HARNESS` / `CTXGUARD_ADAPTERS` / `CTXGUARD_STATE_DIR`，既有 4 个名字不变。
- **hook 输入大门**：4 个 hook 脚本不再直接 `json.load(sys.stdin)`。载荷里一个已知字段都没有
  → 回空答复退出。事件改名、字段改名、脚本被当模块 import，都不会再崩或卡死。
- **`tests/`**：42 项标准库单测（token / 目录规则 / 配置 / 适配器 / 四个 hook 端到端 /
  watchdog `decide()`），外加 `abcompare.py` —— 把同一批载荷喂给新旧两版脚本、逐字节比对的回归网。
- **`docs/adding-a-harness.md`**：加新 harness 的完整步骤与验收清单。

### 说明

- `adapter_codex.py`、`adapter_openai.py` 是**骨架，未在真机验证**，文件头写了待办清单。
  没验证过的能力一律走降级，不会假装能用。
- 仍然零第三方依赖：OpenAI SDK 那条是鸭子类型接入，装了才用。

## v0.1.0

首个版本。

- `split.py` / `paste_split.py`：长粘贴自动落盘、切块，逐块交给子代理处理，原文不进入主上下文。
- `gate.py` / `hook_guard.py`：`PreToolUse` 守卫，拦截整读大文件与裸 `cat` 大文件。
- `resume.py` / `session_resume.py`：生成精简交接单，重启后自动注入。
- `statusline.py`：状态栏显示上下文占用，并产出按会话的用量文件。
- `watchdog.py` / `watchdog.cmd`：会话忙且接近上限时自动重启接力，带 `--max-restarts` 安全绳。
