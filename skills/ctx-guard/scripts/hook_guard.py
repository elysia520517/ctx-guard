#!/usr/bin/env python3
"""ctx-guard / hook_guard.py —— 可选 PreToolUse 提醒（纯提醒，从不拦截）。

挂到 ~/.claude/settings.json 的 PreToolUse(matcher: Bash|Read|Grep|Glob) 上。
它在命令/文件"看起来会倒出大输出"时，往上下文里补一句提醒，让模型自己
加 head / offset，不改变任何调用结果（始终 exit 0，永不 deny）。

设计原则：宁可漏报，不误报；任何异常都静默放行。
"""
import sys, os, json, re

try:                       # Windows 控制台默认 GBK，会把中文提醒打印成乱码
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import harness as _harness     # 适配器 + hook 输入大门

H = None

# 明显会倒大输出的命令特征
BIG_CMD = re.compile(
    r"(\bcat\b|\bless\b|\bmore\b|\bfind\s+/|\bgit\s+log\b(?!.*-n\b)(?!.*--oneline)"
    r"|\bgit\s+diff\b(?!.*--stat)|\bnpm\s+(install|ci)\b|\bpip\s+install\b"
    r"|\bcat\s+\S+\.log\b|\bjournalctl\b(?!.*-n)|\bdmesg\b|\btree\b(?!.*-L)"
    r"|\bwc\s+-l\s+\S+\s*$|\.jsonl\b)",
    re.I)
# 已经有节流手段的，就不提醒
HAS_LIMIT = re.compile(
    r"(\|\s*(head|tail|grep|awk|sed|cut|sort|uniq|wc)\b|\bhead\s+-|\btail\s+-"
    r"|--oneline|-n\s*\d+|--max-count|-q\b|>\s*\S+|2>&1\s*\|)", re.I)


def warn(text):
    # 纯文本输出 = 作为上下文提醒注入（prose 添加请求）
    print("[ctx-guard] " + text)


def main():
    global H
    H = _harness.get_adapter()
    data = _harness.any_stdin_payload()
    if data is None:
        return                      # 不是给我们的输入：静默退出（本脚本从不输出 JSON）
    try:
        name = H.canonical_tool(data.get("tool_name") or "")
        inp = data.get("tool_input") or {}

        if name == "Bash":
            cmd = str(inp.get("command", ""))
            if BIG_CMD.search(cmd) and not HAS_LIMIT.search(cmd):
                warn("这条命令可能倒出很大的输出。建议加节流，例如 "
                     "`| head -60` / `--oneline` / 先 `wc -l` 再挑窗口，"
                     "否则一次就可能吃掉几十 k token。")

        elif name == "Read":
            fp = str(inp.get("file_path", ""))
            if not inp.get("limit") and not inp.get("offset"):
                size = None
                try:
                    size = os.path.getsize(fp)
                except Exception:
                    pass
                if size and size > 120_000:
                    warn("这个文件 %.0f KB，整读会很大。建议带 offset/limit "
                         "只读需要的区间，或先 Grep 定位。" % (size / 1024))
                elif fp.lower().endswith((".jsonl", ".log", ".csv")) and (
                        not size or size > 200_000):
                    warn("读大日志/数据文件建议带 offset/limit 或先 wc -l。")

        elif name in ("Grep", "Glob"):
            # 宽泛搜索往往命中上千行；提醒限定范围
            if not inp.get("glob") and not inp.get("path"):
                warn("这个搜索范围较宽，若命中过多会塞满上下文。"
                     "建议加 glob/path 限定，或用 Explore 子代理只回结论。")
    except Exception:
        pass


if __name__ == "__main__":
    main()
