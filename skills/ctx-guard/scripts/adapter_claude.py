#!/usr/bin/env python3
"""ctx-guard / adapter_claude.py —— Claude Code 适配器（默认、也是目前最完整的一个）。

所有方法都是把**今天已经写死在各个脚本里的行为**原样搬进来，改这里等于改 Claude 侧的行为，
所以除注释外不要顺手"优化"。非 Claude 的适配器照抄本文件的结构即可，见 docs/adding-a-harness.md。

能力一览（未实现的走基类降级）：
  detect / state_dir / locator      —— 目录与定位
  read_transcript                   —— transcript JSONL → 归一化事件流
  context_usage                     —— statusline 落盘的用量看板
  sessions_map / session_status     —— claude agents --json，带 sessions/*.json 兜底
  inject_context / block_tool       —— hookSpecificOutput 信封 + exit 2 拦截
  launch_continue                   —— claude --bg
"""
import glob
import json
import os
import re
import subprocess
import time

import adapter_base
import config
import paths

#: Claude 的"看会话死没死"有两套入口，--json 是主路，sessions/*.json 是兜底
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
UNTRUSTED_HINT = "not trusted"
SUMMARY_PREFIX = "This session is being continued"

#: 上下文占用 ≥ 这个值就提醒"该 /clear 了"
USAGE_FILE_NAME = "ctx-guard-usage.json"
SESSIONS_DIR_NAME = "ctx-guard-sessions"


def _raw_len(blk):
    """块序列化后的字节长度（与旧实现一致：json.dumps 不压缩非 ASCII）。"""
    return len(json.dumps(blk, ensure_ascii=False).encode("utf-8", "ignore"))


class ClaudeAdapter(adapter_base.Adapter):
    name = "claude"

    #: 规范名 → 该 harness 里可能出现的名字
    tool_aliases = {
        "Read": ("Read",), "Write": ("Write",), "Edit": ("Edit",),
        "NotebookEdit": ("NotebookEdit",), "Bash": ("Bash",),
        "Grep": ("Grep",), "Glob": ("Glob",), "Agent": ("Agent", "Task"),
    }

    # ---------------------------------------------------------------- 探测
    def detect(self) -> bool:
        # 宽松探测：只要看得见 Claude Code 的家目录就算（它是默认回退，宁可认错也别漏）
        if os.path.isdir(os.path.join(os.path.expanduser("~"), ".claude")):
            return True
        return any(k.startswith("CLAUDE") for k in os.environ)

    # ------------------------------------------------------------ 目录 / 定位
    def state_dir(self) -> str:
        cfg = config.get("STATE_DIR", "", state_dir=None)
        if cfg:
            return cfg
        return os.path.join(os.path.expanduser("~"), ".claude")

    def locator(self) -> paths.Locator:
        return paths.Locator(state_dir=self.state_dir(), slug=paths.default_slug)

    # --------- 运行时状态文件（ctx-guard 自己的，不是 harness 的）---------
    # usage_file() / sessions_dir() 直接继承基类的"状态目录 + 文件名"实现，
    # 文件名常量在 base 里；这里只保留名字，方便别处引用。
    USAGE_FILE_NAME = USAGE_FILE_NAME
    SESSIONS_DIR_NAME = SESSIONS_DIR_NAME

    # ------------------------------------------------------------ 会话读取
    def read_transcript(self, path):
        """Claude transcript JSONL → 归一化事件流。

        事件形状（消费方只认这几类，详见 adapter_base 的说明）：
          {"kind": "text", "role": "user"|"assistant", "text": str,
           "from": "string"|"block", "is_summary": bool, "ts": str}
          {"kind": "thinking", "text": str, "ts": str}
          {"kind": "tool_use", "id", "name", "input", "raw_len", "ts"}
          {"kind": "tool_result", "tool_use_id", "blocks", "ts"}
          {"kind": "other", "subtype", "raw_len", "ts"}
          {"kind": "_stats", "lines": int, "first_ts": str, "last_ts": str}
              # 最后一条，记录读了多少行、以及**所有行**里首/末带时间戳的那两个
              # （注意：没有内容的行不产出事件，但它的时间戳仍会体现在 first/last_ts）
        """
        nlines = 0
        first_ts = last_ts = ""
        for line in open(path, "rb"):
            nlines += 1
            try:
                d = json.loads(line)
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            ts = d.get("timestamp") or ""
            if ts:
                if not first_ts:
                    first_ts = ts
                last_ts = ts
            msg = d.get("message")
            if not isinstance(msg, dict):
                msg = {}
            role = msg.get("role") or d.get("type") or ""
            content = msg.get("content")

            if isinstance(content, str):
                yield {"kind": "text", "role": role, "text": content, "from": "string",
                       "is_summary": content.startswith(SUMMARY_PREFIX), "ts": ts}
                continue
            if not isinstance(content, list):
                continue

            for blk in content:
                if not isinstance(blk, dict):
                    continue
                t = blk.get("type")
                if t == "tool_use":
                    yield {"kind": "tool_use", "id": blk.get("id"),
                           "name": blk.get("name", "?"),
                           "input": blk.get("input") or {},
                           "raw_len": _raw_len(blk), "ts": ts}
                elif t == "tool_result":
                    yield {"kind": "tool_result",
                           "tool_use_id": blk.get("tool_use_id"),
                           "blocks": list(self._result_blocks(blk.get("content"))),
                           "ts": ts}
                elif t == "thinking":
                    yield {"kind": "thinking", "text": blk.get("thinking", "") or "",
                           "ts": ts}
                elif t == "text":
                    yield {"kind": "text", "role": role, "text": blk.get("text", "") or "",
                           "from": "block", "is_summary": False, "ts": ts}
                else:
                    yield {"kind": "other", "subtype": t, "raw_len": _raw_len(blk),
                           "ts": ts}

        yield {"kind": "_stats", "lines": nlines,
               "first_ts": first_ts, "last_ts": last_ts}

    @staticmethod
    def _result_blocks(content):
        """tool_result 的 content 可能是字符串，也可能是 text/image 块列表。

        非 dict 的块**整块跳过**（与旧实现一致，别改成 coercion）。
        产出 ("text", 文本, 原块) 或 ("image", base64, 原块)。
        """
        if isinstance(content, list):
            blocks = content
        else:
            blocks = [{"type": "text", "text": content}]
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "image":
                src = b.get("source")
                b64 = src.get("data", "") if isinstance(src, dict) else ""
                yield ("image", b64, b)
            else:
                txt = b.get("text")
                yield ("text",
                       txt if txt is not None else json.dumps(b, ensure_ascii=False),
                       b)

    # ---------------------------------------------------------------- 用量
    def context_usage(self, payload=None, max_age_s=60):
        """读 statusline.py 落盘的用量看板；过期或缺失返回 None。"""
        p = self.usage_file()
        try:
            with open(p, encoding="utf-8") as fh:
                u = json.load(fh)
        except Exception:
            return None
        try:
            if time.time() - u.get("ts", 0) >= max_age_s:
                return None
        except Exception:
            return None
        return u if isinstance(u, dict) else None

    # ------------------------------------------------------------ 会话存活
    def sessions_map(self):
        """session_id → 记录。`claude agents --json` 为主，sessions/*.json 兜底。

        取不到返回 None（区别于"取到了但是空的"）。
        """
        try:
            p = subprocess.run(["claude", "agents", "--json"], capture_output=True,
                               text=True, timeout=30)
            arr = json.loads(p.stdout or "[]")
            if isinstance(arr, list):
                m = {s.get("sessionId"): s for s in arr if isinstance(s, dict)}
                if m:
                    return m
        except Exception:
            pass
        return self._sessions_from_files()

    def _sessions_from_files(self):
        """兜底：Claude Code 会在 ~/.claude/sessions/<pid>.json 里维护一份注册表。"""
        out = {}
        try:
            for f in glob.glob(os.path.join(self.state_dir(), "sessions", "*.json")):
                try:
                    with open(f, encoding="utf-8") as fh:
                        d = json.load(fh)
                except Exception:
                    continue
                if isinstance(d, dict) and d.get("sessionId"):
                    out[d["sessionId"]] = d
        except Exception:
            return None
        return out or None

    def session_status(self, session_id):
        m = self.sessions_map() or {}
        return (m.get(session_id) or {}).get("status")

    # ------------------------------------------------------------ hook 协议
    def inject_context(self, payload, text, default_event=None):
        """Claude Code 的上下文注入走 `hookSpecificOutput.additionalContext` 信封。"""
        event = None
        if isinstance(payload, dict):
            event = payload.get("hook_event_name") or payload.get("hookEventName")
        event = event or default_event or "PreToolUse"
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event, "additionalContext": text}}))

    def block_tool(self, payload, message) -> int:
        """exit 2 + stderr —— stderr 会被回传给模型，所以必须是 UTF-8。"""
        import sys
        sys.stderr.write(message)
        return 2

    def can_block(self) -> bool:
        return True

    # ------------------------------------------------------------ 起会话接力
    def launch_continue(self, cwd, seed, model=None):
        cmd = ["claude", "--bg"] + (["--model", model] if model else []) + [seed]
        try:
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=180)
        except Exception as e:
            return "error", "起会话失败：%r" % e
        out = (p.stdout or "") + (p.stderr or "")
        if UNTRUSTED_HINT in out.lower():
            return "untrusted", out.strip()[:200]
        m = UUID_RE.findall(out)
        return "launched", (m[-1] if m else "LAUNCHED")
