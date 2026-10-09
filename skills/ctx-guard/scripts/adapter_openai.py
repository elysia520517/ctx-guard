#!/usr/bin/env python3
"""ctx-guard / adapter_openai.py —— OpenAI Agents SDK（`openai-agents`，Python 库，不是 CLI）。

这是"将来兼容 OpenAI"的第一块地基。注意它和 Claude Code 的形态差别很大：

  · Claude Code 是**外部 CLI**，hook = 子进程 + stdin/stdout + 退出码；
  · OpenAI Agents SDK 是**进程内库**，hook = 一个 `hooks=` 对象上的回调方法。

所以本适配器里真正有用的入口是 `make_hooks()` —— 你在 `Runner.run(..., hooks=...)` 里传它，
它会统计用量、并在上下文将满/工具即将倒出大输出时给出提醒。

**为什么全用反射、不 import 具体类名**：这个 SDK 的 hook 方法名一直在改（主干已把
`on_agent_start` / `on_start` 改名过）。所以这里的策略是"**找得到就挂、找不到就降级**" ——
这本身就是本项目"版本兼容"主题的最小示范。也因此本文件**零第三方 import**，
只有在你用 `make_hooks()` 且环境里真的装了 `agents` 时，才会去 import。

能力一览：
  detect            —— 环境里能不能 `import agents`
  context_usage     —— 由 make_hooks() 在 on_llm_end 里累积（SDK 的 usage 字段）
  block_tool        —— 在 on_tool_start 里抛错（鸭子类型；SDK 尚未提供官方"拒绝"语义）
  inject_context    —— 无信封，直接打印（基类默认）
  其余              —— 不适用，走基类降级
"""
import importlib.util
import os
import re

import adapter_base

#: 一个 agent 跑完后，上下文占用到多少就该提醒了
WARN_PCT = 0.80

#: 各版本用过的 hook 方法名（新名在前）。反射时逐个试。
_HOOK_METHODS = (
    "on_agent_start", "on_agent_end",
    "on_tool_start", "on_tool_end",
    "on_llm_start", "on_llm_end",
    "on_handoff",
    # 旧版 / 别名
    "on_start", "on_end",
)


def sdk_available() -> bool:
    """环境里是否装了 openai-agents（不 import，只查 spec）。"""
    try:
        return importlib.util.find_spec("agents") is not None
    except Exception:
        return False


class OpenAIAdapter(adapter_base.Adapter):
    name = "openai"

    # ---------------------------------------------------------------- 探测
    def detect(self) -> bool:
        if sdk_available():
            return True
        # 也认一下常见的"我在这个 SDK 的进程里"的环境指纹
        return any(k.startswith("OPENAI_AGENTS") for k in os.environ)

    # ------------------------------------------------------------ 目录 / 定位
    def state_dir(self) -> str:
        # 这个 harness 没有 ~/.claude 那种约定，用 ctx-guard 自己的状态目录
        return os.path.join(os.path.expanduser("~"), ".ctxguard", "openai")

    # ------------------------------------------------------------ 会话读取
    # 不适用：Agents SDK 不落盘 transcript 文件（会话内容在调用方手里的 list）。
    # 需要统计时请直接用 make_hooks() 的 running 计数。

    # ------------------------------------------------------------ hook 工厂
    def make_hooks(self, on_usage=None, on_block=None):
        """造一个可以传给 `Runner.run(agent, input, hooks=...)` 的对象。

        只挂**这一版 SDK 真的有**的那些方法名（反射自 `agents.lifecycle.RunHooks`，取不到
        就退回一份保守名单），所以换 SDK 版本通常不用改这里。

        参数：
          on_usage(dict)   每次 LLM 调用结束回调一次，带累积用量
          on_block(str)    工具即将倒出大输出时回调；返回 True 表示"并拦截"（抛错）
        """
        available = self._available_hook_names()
        return _DuckHooks(self, available, on_usage=on_usage, on_block=on_block)

    @staticmethod
    def _available_hook_names():
        """反射 SDK 的 hooks 类，拿到它真正有哪些 on_* 方法；失败则用保守名单。"""
        try:
            mod = importlib.import_module("agents.lifecycle")
            for attr in dir(mod):
                obj = getattr(mod, attr)
                if isinstance(obj, type) and hasattr(obj, "on_agent_start"):
                    names = {n for n in dir(obj) if n.startswith("on_")}
                    if names:
                        return names
        except Exception:
            pass
        try:
            mod = importlib.import_module("agents")
            for attr in dir(mod):
                obj = getattr(mod, attr)
                if isinstance(obj, type) and any(n in dir(obj) for n in _HOOK_METHODS):
                    names = {n for n in dir(obj) if n.startswith("on_")}
                    if names:
                        return names
        except Exception:
            pass
        return set(_HOOK_METHODS)

    # ---------------------------------------------------------------- 用量
    def context_usage(self, tracker=None):
        return None if tracker is None else tracker.as_usage()

    # ------------------------------------------------------------ hook 协议
    def can_block(self) -> bool:
        # 鸭子类型拦截（抛错），不是官方语义 —— 标记为"能"，由使用方自负
        return True

    def block_tool(self, payload, message) -> int:
        """拦截 = 抛异常（没有退出码这一说）。"""
        raise OpenAIBlocked(message)

    # ------------------------------------------------------------ 起会话接力
    # 不适用：SDK 没有"起一个新会话"的外部对象，接力由调用方自己再跑一次 Runner.run。


class OpenAIBlocked(RuntimeError):
    """ctx-guard 在这个 harness 里用异常表示"拒绝这次工具调用"。"""


class _UsageTracker:
    """从 SDK 的 usage 对象里抠 token 数并累积。字段名各版本不一，所以全用 getattr 试探。"""

    _IN = ("input_tokens", "prompt_tokens", "inputTokens")
    _OUT = ("output_tokens", "completion_tokens", "outputTokens")
    _TOTAL = ("total_tokens", "totalTokens")

    def __init__(self):
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.model = ""
        self.last_error = ""

    @staticmethod
    def _pick(obj, names, default=0):
        for n in names:
            v = getattr(obj, n, None)
            if isinstance(v, (int, float)):
                return int(v)
        return default

    def add(self, response_or_usage):
        self.calls += 1
        u = response_or_usage
        for attr in ("usage", "response", "raw_response"):
            if u is not None and hasattr(u, attr):
                u = getattr(u, attr)
                break
        if u is None:
            return self
        self.input_tokens += self._pick(u, self._IN)
        self.output_tokens += self._pick(u, self._OUT)
        self.total_tokens += self._pick(u, self._TOTAL,
                                        self.input_tokens + self.output_tokens)
        m = getattr(u, "model", None)
        if isinstance(m, str) and m:
            self.model = m
        return self

    def as_usage(self):
        return {"calls": self.calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "total_tokens": self.total_tokens,
                "model": self.model}


class _DuckHooks:
    """一版 hooks：只挂反射得到的那些方法名，其余不挂。"""

    def __init__(self, adapter, available, on_usage=None, on_block=None):
        self._adapter = adapter
        self._available = set(available or ())
        self._on_usage = on_usage
        self._on_block = on_block
        self.usage = _UsageTracker()
        self.blocked = []
        self.notes = []
        self._install()

    # ---- 把方法挂成实例属性（只挂 SDK 真有的那些）----
    def _install(self):
        table = {
            "on_llm_end": self._llm_end,
            "on_tool_start": self._tool_start,
            "on_agent_end": self._agent_end,
        }
        # 旧别名也认（哪个存在于 SDK 就挂哪个）
        aliases = {"on_end": self._llm_end}
        for name, fn in list(table.items()) + list(aliases.items()):
            if name in self._available:
                setattr(self, name, fn)

    # ---- 回调实现（签名用 *a/**kw，避免被各版本的参数表绑死）----
    def _llm_end(self, *a, **kw):
        payload = kw.get("response") or (a[0] if a else None)
        self.usage.add(payload)
        if self._on_usage:
            try:
                self._on_usage(self.usage.as_usage())
            except Exception:
                pass

    def _tool_start(self, *a, **kw):
        """工具即将执行：估算它会倒出多大，超过阈值就提醒（并可选拦截）。"""
        ctx = kw.get("context") or (a[0] if a else None)
        tool = kw.get("tool") or kw.get("agent")
        name = getattr(tool, "name", None) or kw.get("tool_name") or "?"
        inp = {}
        for src in (ctx, tool):
            v = getattr(src, "tool_input", None) or getattr(src, "arguments", None)
            if isinstance(v, dict):
                inp = v
                break
        size = _probe_size(inp)
        if size is None:
            size = _probe_size_from_env()
        if size is None:
            return
        note = ("tool %s: 预计输出约 %.0f KB" % (name, size / 1024))
        if self._on_block and self._on_block(note):
            self.blocked.append(note)
            raise OpenAIBlocked("[ctx-guard] 拒绝无节流的大输出：%s" % note)
        self.notes.append(note)

    def _agent_end(self, *a, **kw):
        if self.usage.calls and self._on_usage:
            try:
                self._on_usage(self.usage.as_usage())
            except Exception:
                pass

    # ---- 自述 ----
    def installed_methods(self):
        return sorted(n for n in self.__dict__ if n.startswith("on_"))


def _probe_size(inp):
    """从工具参数里猜目标文件大小（Claude 是 file_path，别的先按常见键试）。"""
    if not isinstance(inp, dict):
        return None
    for k in ("file_path", "path", "filename"):
        p = inp.get(k)
        if isinstance(p, str) and p:
            try:
                return os.path.getsize(p)
            except Exception:
                return None
    return None


def _probe_size_from_env():
    """允许调用方通过环境变量告知"这一步会倒出多大"，用于没有文件路径的工具。"""
    v = os.environ.get("CTXGUARD_TOOL_BYTES")
    if not v:
        return None
    try:
        return int(float(re.sub(r"[^0-9.]", "", v)))
    except Exception:
        return None
