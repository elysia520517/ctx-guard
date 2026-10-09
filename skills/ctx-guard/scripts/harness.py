#!/usr/bin/env python3
"""ctx-guard / harness.py —— 探测当前跑在哪个 AI 编码工具下，并给出对应适配器。

两件事：

1. **适配器选择**（`get_adapter`）。优先级：
     显式 CTXGUARD_HARNESS=<name>  >  自动探测（按注册表顺序，谁 detect() 谁上）
     >  Claude Code（默认回退，保证既有行为一点不变）
   `CTXGUARD_ADAPTERS=claude,codex` 可以限制自动探测的候选集。

2. **hook 输入大门**（`any_stdin_payload`）。这是"版本兼容"的关键：hook 脚本不能因为
   事件改名、字段改名、或"根本没有 stdin"就崩掉或挂死。做法是先 `select` 一次 stdin，
   可读才读；读不到任何已知键就返回 None，调用方回一个空 JSON `{}` 然后 exit 0。
   → 事件名改了：读不到已知键，空回复，不炸。
   → 被别的工具当模块 import（没有 stdin）：select 不可读，空回复，不炸。
   → 正常情况：与原来完全一致。
"""
import importlib
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import adapter_base  # noqa: E402  (必须在 sys.path 调整之后)
import config        # noqa: E402
import paths         # noqa: E402

#: (harness 名, 模块名)。Claude 放最前——它是默认且最完整的实现。
#: 加新 harness 就在这儿加一行，其余脚本一行都不用动。
REGISTRY = (
    ("claude", "adapter_claude"),
    ("codex", "adapter_codex"),
    ("openai", "adapter_openai"),
)

#: hook 载荷里"认识的"键。一个都没有 → 认为不是喂给我们的 payload。
KNOWN_KEYS = frozenset((
    "hook_event_name", "hookEventName", "tool_name", "tool_input",
    "prompt", "session_id", "sessionId", "transcript_path", "cwd",
    "context_window", "message", "event", "type",
))

_lock = threading.Lock()
_cache = {}


# --------------------------------------------------------------------- 加载
def _load_adapter(name, module_name):
    """惰性加载一个适配器；模块不存在/导入出错 → None（静默降级）。"""
    try:
        mod = importlib.import_module(module_name)
    except Exception:
        return None
    for attr in dir(mod):
        obj = getattr(mod, attr)
        if (isinstance(obj, type) and issubclass(obj, adapter_base.Adapter)
                and obj is not adapter_base.Adapter
                and getattr(obj, "name", "") == name):
            try:
                return obj()
            except Exception:
                return None
    return None


def _candidates():
    allow = config.listing("ADAPTERS")
    reg = [r for r in REGISTRY if not allow or r[0] in allow]
    return reg or list(REGISTRY)


def default_adapter_name():
    reg = list(REGISTRY)
    return reg[0][0] if reg else "claude"


def get_adapter(force=None):
    """拿到当前 harness 的适配器实例。永不返回 None。"""
    key = force or config.get("HARNESS", "") or "<auto>"
    with _lock:
        if key in _cache:
            return _cache[key]
    a = _resolve(force)
    with _lock:
        _cache[key] = a
    return a


def _resolve(force):
    explicit = (force or config.get("HARNESS", "") or "").strip().lower()

    if explicit:
        for nm, mod in REGISTRY:
            if nm == explicit:
                a = _load_adapter(nm, mod)
                if a is not None:
                    return a
        # 显式点了不存在的 harness：退回默认，但不静默 —— 打个警告到 stderr
        sys.stderr.write("[ctx-guard] 未知 harness %r，回退 %s\n"
                         % (explicit, default_adapter_name()))
        return _fallback()

    for nm, mod in _candidates():
        a = _load_adapter(nm, mod)
        if a is None:
            continue
        try:
            if a.detect():
                return a
        except Exception:
            continue
    return _fallback()


def _fallback():
    a = _load_adapter(*REGISTRY[0]) if REGISTRY else None
    return a if a is not None else adapter_base.Adapter()


# --------------------------------------------------------------- hook 输入大门
def _stdin_obj():
    try:
        return sys.stdin if sys.stdin is not None and not sys.stdin.closed else None
    except Exception:
        return None


def stdin_is_tty() -> bool:
    s = _stdin_obj()
    if s is None:
        return True                     # 没有 stdin：当作"没人喂我们"
    try:
        return bool(s.isatty())
    except Exception:
        return False


def _read_stdin(timeout=1.0):
    """带超时地读 stdin（Windows 上 select() 会因 WSAStartup 未初始化而抛错，故用线程兜底）。"""
    s = _stdin_obj()
    if s is None:
        return ""
    # POSIX 上 select 又准又省事，优先用
    if not sys.platform.startswith("win"):
        try:
            import select
            if not select.select([s], [], [], timeout)[0]:
                return ""
        except Exception:
            pass
        try:
            return s.read() or ""
        except Exception:
            return ""
    # Windows：线程里读，主线程按时收工（daemon 线程不会拖住退出）
    if s is sys.stdin:
        # 只在"不是终端"时读，避免吃掉交互输入
        if stdin_is_tty():
            return ""
    box = {"raw": "", "done": False}

    def _rd():
        try:
            box["raw"] = s.read() or ""
        except Exception:
            box["raw"] = ""
        finally:
            box["done"] = True

    t = threading.Thread(target=_rd, daemon=True)
    t.start()
    t.join(timeout)
    return box["raw"] if box["done"] else ""


def read_payload(timeout=1.0):
    """读 stdin 的 JSON。没有 stdin / 是终端 / 不是 JSON / 不是非空对象 → None。"""
    if stdin_is_tty():
        return None
    raw = _read_stdin(timeout)
    if not raw or not raw.strip():
        return None
    try:
        d = json.loads(raw)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    return d if d else None


def stdin_readable(timeout=1.0) -> bool:
    """stdin 上有没有东西可读（=read_payload 拿不拿得到）。"""
    if stdin_is_tty():
        return False
    if not sys.platform.startswith("win"):
        s = _stdin_obj()
        if s is None:
            return False
        try:
            import select
            return bool(select.select([s], [], [], timeout)[0])
        except Exception:
            return True
    # Windows：没有非阻塞探测手段，交给 read_payload 的线程超时来兜
    return True


def looks_like_hook(d) -> bool:
    if not isinstance(d, dict):
        return False
    return any(k in d for k in KNOWN_KEYS)


def any_stdin_payload(timeout=1.0, require_known=True):
    """hook 脚本的统一入口：拿到 payload 就返回 dict，否则 None。

    `require_known=True` 时，载荷里必须至少有一个 KNOWN_KEYS 里的键，否则当作
    "不是给我们的输入"（事件被改名 / 被别的程序调用），返回 None 让调用方空回复退出。
    """
    d = read_payload(timeout)
    if d is None:
        return None
    if require_known and not looks_like_hook(d):
        return None
    return d


def emit_empty():
    """空回复：这个 harness 的"我什么都不做"该输出什么。

    Claude Code 的空 stdout 本身就是合法的 no-op，所以**保持原样不输出**，
    这样改造前后逐字节一致。需要显式 JSON 的 harness 可在适配器里改 `empty_reply()`。
    """
    try:
        txt = get_adapter().empty_reply()
    except Exception:
        txt = ""
    if txt:
        sys.stdout.write(txt if txt.endswith("\n") else txt + "\n")


# --------------------------------------------------------------------- 便利
def state_dir() -> str:
    return state_dir_of(get_adapter())


def state_dir_of(adapter) -> str:
    try:
        return adapter_base.existing_dir_or(adapter.state_dir(), paths.default_state_dir())
    except Exception:
        return paths.default_state_dir()


def locator():
    try:
        return get_adapter().locator()
    except Exception:
        return paths.default()


def describe() -> dict:
    """给 `--doctor` 之类用的自述。"""
    a = get_adapter()
    caps = [c for c in ("read_transcript", "context_usage", "sessions_map",
                        "inject_context", "block_tool", "launch_continue")
            if adapter_base.supports(a, c)]
    return {"harness": getattr(a, "name", "?"),
            "state_dir": state_dir_of(a),
            "capabilities": caps,
            "candidates": [n for n, _ in _candidates()]}
