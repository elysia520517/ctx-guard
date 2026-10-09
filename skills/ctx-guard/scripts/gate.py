#!/usr/bin/env python3
"""ctx-guard / gate.py —— PreToolUse 拦截闸门。

只在**已知道输出会很大**时才真正拦截（exit 2），其余一律放行：
  - Read 一个 >LIMIT 的文件且没带 offset/limit  -> 拦
  - Bash 里 `cat/type/Get-Content` 一个 >LIMIT 的文件且无 head/重定向 -> 拦
  - 宽泛搜索 / 无节流的 git log/diff 等 -> 只提醒（additionalContext），不拦

拦截时把"该怎么做"写进 stderr —— 模型会收到并改用 offset/limit 重试，
所以不会卡死，只是多一轮。阈值可用环境变量调：
  CTXGUARD_READ_KB   默认 200
  CTXGUARD_BLOCK     默认 on；设 off 则退化为纯提醒（只 inject additionalContext）

挂 settings.json 的 hooks.PreToolUse，matcher "Read|Bash"。
"""
import sys, os, json, re, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")   # exit2 的 stderr 要喂给模型，必须 UTF-8
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import harness as _harness     # 适配器 + hook 输入大门
import config as _config       # 统一配置读取

H = None                       # 延迟到 main 里取，避免 import 时就探测

READ_KB = _config.number("READ_KB", 200)
LIMIT = int(READ_KB * 1024)
BLOCK = _config.flag("BLOCK", "on")

BIG_DUMP = re.compile(r"^\s*(cat|type|Get-Content|gc)\s+(?:-[A-Za-z]+\s+)*[\"']?([^\"'|><\s]+)",
                      re.I)
HAS_THROTTLE = re.compile(
    r"(\|\s*(head|tail|grep|awk|sed|cut|sort|uniq|wc|more|less)\b"
    r"|\bhead\s+-|\btail\s+-|>\s*\S+|2>&1\s*\||\|)", re.I)

# 图片：单张封顶 ~1600 tok，文件再大也不该拦（且 offset/limit 对图片无意义）
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tif", ".tiff")


def usage_note():
    """若用量看板文件新鲜且占用高，附一句。"""
    try:
        u = H.context_usage(max_age_s=60)
        if not u:
            return ""
        p = u.get("used_percentage")
        if not isinstance(p, (int, float)):
            return ""
        if p >= 80:
            return " 另：当前上下文已占 %.0f%%，这一轮干完就 /clear（先 digest.py）。" % p
        if p >= 60:
            return " 另：上下文已 %.0f%%，注意收着干。" % p
    except Exception:
        pass
    return ""


def block_exit(msg):
    sys.stderr.write("[ctx-guard] " + msg + usage_note() + "\n")
    sys.exit(2)


def allow_ctx(msg):
    # 不拦，只把提醒作为附加上下文注入
    H.inject_context(None, "[ctx-guard] " + msg, default_event="PreToolUse")
    sys.exit(0)


def size_of(p):
    try:
        return os.path.getsize(p)
    except Exception:
        return None


def main():
    global H
    H = _harness.get_adapter()
    if not BLOCK:
        # 只提醒不拦的形态：即便配置成 off，也走同一套注入
        pass

    # hook 输入大门：拿不到"像是喂给我们的"载荷就直接空回复退出（版本改名/被当模块导入都不炸）
    d = _harness.any_stdin_payload()
    if d is None:
        _harness.emit_empty()
        return

    raw_name = d.get("tool_name") or ""
    name = H.canonical_tool(raw_name)      # 各 harness 的工具名映射到规范名
    inp = d.get("tool_input") or {}
    cwd = d.get("cwd") or os.getcwd()

    if name == "Read":
        fp = str(inp.get("file_path") or "")
        low = fp.lower()
        if low.endswith(IMG_EXT):
            return          # 图片单张封顶 ~1600 tok，offset/limit 无意义，永远放行
        if (not inp.get("offset") and not inp.get("limit") and fp
                and not low.endswith((".jsonl", ".log"))):   # 日志类由 hook_guard 提醒
            n = size_of(fp)
            if n and n > LIMIT:
                if BLOCK:
                    block_exit("拒绝整读：%s 有 %.0f KB（~%s tok），一次装进上下文太亏。"
                               "请带 offset/limit 只读需要的区间，或先 Grep 定位后局部读。"
                               % (os.path.basename(fp), n / 1024, format(int(n / 3.6), ",")))
                allow_ctx("文件 %.0f KB，建议带 offset/limit。" % (n / 1024))

    elif name == "Bash":
        cmd = str(inp.get("command") or "")
        m = BIG_DUMP.match(cmd)
        if m and not HAS_THROTTLE.search(cmd):
            target = m.group(2)
            if not os.path.isabs(target):
                target = os.path.join(cwd, target)
            n = size_of(target)
            if n and n > LIMIT:
                if BLOCK:
                    block_exit("拒绝无节流大输出：`%s` 会倒出 %.0f KB。"
                               "请改成 `%s | head -100` 或先 `wc -l` 再取窗口。"
                               % (m.group(1), n / 1024, m.group(0).strip()))
                allow_ctx("这条命令会倒出 %.0f KB，建议加 | head。" % (n / 1024))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        pass
