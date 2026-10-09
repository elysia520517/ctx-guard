#!/usr/bin/env python3
"""ctx-guard / adapter_codex.py —— Codex CLI（OpenAI）适配器。

**状态：骨架，未在真机验证过。** 本机没装 codex，所以这个文件只做两件确定的事：
  1. `detect()`（看环境指纹），让 Codex 用户被正确识别成 codex 而不是 claude；
  2. 把**从 openai/codex 源码里读出来的**约定写在这里当待办清单，其余能力一律走基类
     降级（只提醒、不起会话）。**不要**在真机跑通前把下面任何一条改成"已完成"。

已知的 Codex 约定（读源码所得，仍需逐条实测）：
  · 配置目录   ~/.codex/          配置 config.toml（CONFIG_TOML_FILE）
  · 指令文件   AGENTS.md（项目根逐级向下拼接到 cwd，另可放 AGENTS.override.md）
  · 通知钩子   ① 新式原生 hook：事件名与 Claude 基本同名（PreToolUse / PostToolUse /
               SessionStart / UserPromptSubmit / PreCompact / Stop / Interrupt …），
               同样是"stdin 收 JSON、exit 2 拦截"的约定，还能从配置目录里的 hooks.json 加载；
               ② 旧式 notify：`notify = ["<可执行程序>", ...]`，程序收到的**最后一个 argv
               就是一段 JSON**（{"type":"agent-turn-complete", "thread-id", "cwd", …}）。
               旧式是**事后通知**，拦不住工具调用。
  · 会话记录   ~/.codex/sessions/rollout-<时间戳>-<uuid>.jsonl（按 年/月/日 分目录），
               另有 ~/.codex/archived_sessions/ 与 SQLite 状态库
  · 起会话     `codex exec "<prompt>"`（非交互）

对照能力表，Codex 要补的（每条都要实测后再写实现）：
  detect            环境指纹（这条已写）
  locator           rollout 的目录树（已知形状，但"哪个文件对应哪个会话"要实测）
  read_transcript   rollout JSONL → 归一化事件流
  context_usage     ContextWindowTokenStatus（active_context_tokens 等）是否落盘
  sessions_map      没有 `agents --json` 等价物，可能要扫 rollout 的 mtime
  launch_continue   `codex exec`
  inject_context    有原生 hook 的话可用 additionalContext；否则退回 AGENTS.md / stdout
  block_tool        只有原生 PreToolUse 能拦；走旧式 notify 时必须返回"不支持"
"""
import glob
import os

import adapter_base
import paths


class CodexAdapter(adapter_base.Adapter):
    name = "codex"

    # ---------------------------------------------------------------- 探测
    def detect(self) -> bool:
        # 明确的 Codex 环境指纹先认；再退一步看配置目录存在且没有 Claude 的痕迹
        for k in os.environ:
            if k.startswith("CODEX"):
                return True
        if _codex_home():
            return not os.path.isdir(os.path.join(os.path.expanduser("~"), ".claude"))
        return False

    # ------------------------------------------------------------ 目录 / 定位
    def state_dir(self) -> str:
        return _codex_home()

    def locator(self) -> paths.Locator:
        # rollout 散在 sessions/<年>/<月>/<日>/ 下面，不是"一个 slug 目录一个会话"，
        # 所以默认的 Locator 只能算个占位 —— 真正实现要覆写 transcripts()/newest()。
        return paths.Locator(state_dir=self.state_dir(),
                             project_subdir="sessions", slug=_codex_slug)

    def newest_rollout(self):
        """按 mtime 取最新的 rollout 文件（**未实测**，先按已知路径形状找）。"""
        pat = os.path.join(self.state_dir(), "sessions", "**", "rollout-*.jsonl")
        files = glob.glob(pat, recursive=True)
        if not files:
            return None
        return max(files, key=lambda p: os.path.getmtime(p))


def _codex_home() -> str:
    """CODEX_HOME 优先（官方支持），否则 ~/.codex。"""
    env = os.environ.get("CODEX_HOME")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".codex")


def _codex_slug(cwd) -> str:
    """占位实现：rollout 不用 slug 分目录，实测后按真实规则改或直接删掉。"""
    return paths.default_slug(cwd)
