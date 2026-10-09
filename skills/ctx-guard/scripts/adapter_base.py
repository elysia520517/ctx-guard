#!/usr/bin/env python3
"""ctx-guard / adapter_base.py —— harness 适配器的基类与"归一化事件流"约定。

**设计原则：能力是可选的。** 子类能实现多少方法就实现多少；调用方一律先用
`supports(adapter, "能力名")` 问一句，没有就降级（只提醒、只记日志、或直接跳过），
绝不因为某个 harness 缺某个能力而整体失效。

为什么要这样：各 harness 的 hook/事件/存储差异极大，而且**还会变**（同一个 SDK 的
hook 方法名在不同版本里都改过）。把"必须有"变成"最好有"，兼容性才不是一句口号。

## 归一化事件流

`read_transcript(path)` 产出一串事件字典，各 harness 自己负责把自家格式翻译过来。
消费方（scan.py / digest.py）只认这套字段：

  {"kind": "user",        "text": str,  "ts": str, "is_summary": bool}
  {"kind": "assistant",   "text": str,  "ts": str}
  {"kind": "thinking",    "text": str,  "ts": str}
  {"kind": "tool_use",    "id": str, "name": str, "input": dict,
                          "raw_len": int, "ts": str}
  {"kind": "tool_result", "tool_use_id": str, "blocks": [(kind, payload), ...],
                          "ts": str}

  blocks 里 kind="text" 时 payload 是文本，kind="image" 时 payload 是 base64 串。
  字节/token 的具体算法留在消费方（见 tokens.py），这样换 harness 不会改变统计口径。
"""
import os

import paths


class Adapter:
    """所有 harness 适配器的基类。默认实现全部是"安全降级"。"""

    name = "base"
    #: 该 harness 里"读/搜/跑"类工具的名字 → ctx-guard 的规范名映射
    tool_aliases = {}

    # ---------------------------------------------------------------- 探测
    def detect(self) -> bool:
        """当前进程是否运行在本 harness 下。基类一律 False。"""
        return False

    # ------------------------------------------------------------ 目录 / 定位
    def state_dir(self) -> str:
        return paths.default_state_dir()

    def state_file(self, name) -> str:
        """ctx-guard 自己的运行时文件放在状态目录下（不属于 harness）。"""
        return os.path.join(self.state_dir(), name)

    def usage_file(self) -> str:
        """全局用量看板（最近一次 statusline 落盘的）。"""
        return self.state_file("ctx-guard-usage.json")

    def sessions_dir(self) -> str:
        """按会话分档的用量看板目录（多开会话时不互相覆盖）。"""
        return self.state_file("ctx-guard-sessions")

    def locator(self) -> paths.Locator:
        return paths.Locator(state_dir=self.state_dir())

    def project_dir(self, cwd=None) -> str:
        return self.locator().project_dir(cwd)

    # ------------------------------------------------------------ 会话读取
    def supports_transcripts(self) -> bool:
        return self.read_transcript.__func__ is not Adapter.read_transcript

    def read_transcript(self, path):
        """产出归一化事件。基类不支持 → 产出空。"""
        return iter(())

    # ---------------------------------------------------------------- 用量
    def context_usage(self, payload=None):
        """当前上下文占用（dict，至少含 used_percentage）或 None。"""
        return None

    def session_status(self, session_id):
        """"busy" / "idle" / 其他；取不到返回 None。"""
        return None

    def sessions_map(self):
        """session_id -> 会话记录 dict；取不到返回 None（区别于空 dict）。"""
        return None

    # ------------------------------------------------------------ hook 协议
    def inject_context(self, payload, text):
        """把一段提示注入模型上下文的**默认**实现：直接打印纯文本。

        Claude Code 有 `hookSpecificOutput.additionalContext` 信封，会覆盖本方法；
        没有信封的 harness 就用这里的"打印到 stdout"约定。
        """
        print(text)

    def block_tool(self, payload, message) -> int:
        """拦截一次工具调用。返回进程退出码。

        不能拦截的 harness 返回 0（=放行），由调用方决定是否退化成"只提醒"。
        """
        return 0

    def can_block(self) -> bool:
        return self.block_tool.__func__ is not Adapter.block_tool

    def empty_reply(self) -> str:
        """hook 无话可说时该往 stdout 写什么。空串 = 什么都不写（多数 CLI 这样最安全）。"""
        return ""

    # ------------------------------------------------------------ 起会话接力
    def launch_continue(self, cwd, seed, model=None):
        """起一个新会话继续干活。返回 (状态, 说明)。

        状态取值： "launched" | "dry-run" | "untrusted" | "unsupported" | "error"
        """
        return "unsupported", "本 harness 不支持自动起会话"

    # ---------------------------------------------------------------- 工具名
    def canonical_tool(self, name) -> str:
        """把 harness 的工具名叫回 ctx-guard 的规范名（未知则原样返回）。"""
        for canon, aliases in (self.tool_aliases or {}).items():
            if name == canon or name in aliases:
                return canon
        return name


def supports(adapter, cap) -> bool:
    """适配器是否**真正**实现了某项能力（而非继承基类的空实现）。"""
    fn = getattr(adapter, cap, None)
    if fn is None:
        return False
    base_fn = getattr(Adapter, cap, None)
    if base_fn is None:
        return True
    wrapped = getattr(fn, "__func__", None)
    return wrapped is not base_fn


def state_dir_for(adapter=None) -> str:
    return (adapter.state_dir() if adapter else paths.default_state_dir())


def existing_dir_or(path, fallback):
    """目录存在就用它，否则用 fallback —— 便于"缺 ~/.claude 也不崩"。"""
    try:
        if path and os.path.isdir(path):
            return path
    except Exception:
        pass
    return fallback
