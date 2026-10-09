#!/usr/bin/env python3
"""ctx-guard / digest.py —— 把一个巨型会话压成一页交接单（用于 /clear 之前）。

这是"断了之后能接上"的那一环：把 85 MB 的 transcript 里的
**工具输出全部丢掉**，只留：用户说过什么、动过哪些文件、跑过哪些命令、
卡在哪一步。产出 8 KB 级别，可以直接塞进新会话当上下文。

用法（只读）:
  python -I digest.py                    # 当前项目最新会话
  python -I digest.py <transcript.jsonl>
  python -I digest.py --max-bytes 12000  # 调大输出上限
  python -I digest.py --stdout           # 打到屏幕而不是写文件
默认写文件:  <项目目录>/CTX_DIGEST.md  （并打印统计）
"""
import sys, os, json, re, glob, collections, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import harness as _harness          # harness 探测 + 适配器
import paths as _paths              # transcript / 项目目录定位（唯一实现）


def project_dir(cwd=None):
    return _harness.locator().project_dir(cwd)


def newest_transcript(pdir):
    return _harness.locator().newest_transcript(pdir)


def clip(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n] + " …"


def ts(d):
    # 兼容两种入参：原始 transcript 行（key=timestamp）与归一化事件（key=ts）
    t = d.get("timestamp") or d.get("ts")
    if not t:
        return ""
    try:
        return time.strftime("%m-%d %H:%M", time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return t[:16]


def digest(path, max_bytes=8192):
    prompts = []          # (ts, text)
    files = collections.Counter()
    wrote = collections.Counter()
    cmds = []
    asks = []             # 子代理任务
    last_assistant = []
    first_ts = last_ts = ""
    n_tool = 0

    # 走适配器的归一化事件流（换 harness 只换适配器，这里的取舍逻辑不动）
    A = _harness.get_adapter()
    for ev in A.read_transcript(path):
        k = ev.get("kind")
        if k == "_stats":
            first_ts = ts({"timestamp": ev.get("first_ts") or ""})
            last_ts = ts({"timestamp": ev.get("last_ts") or ""})
            break

        if k == "text":
            content = ev.get("text") or ""
            # 与旧实现严格等价：**只有字符串形式的 user 内容**才算"用户说过的话"；
            # 列表里的 text 块一律只喂给"最后几段助手正文"。别顺手改成 role 判断。
            if ev.get("from") == "string" and ev.get("role") == "user":
                t = clip(content, 400)
                if t and not t.startswith("<") and "system-reminder" not in t[:40]:
                    prompts.append((ts(ev), t))
            elif ev.get("role") == "assistant" and content.strip():
                last_assistant.append(clip(content, 300))
            continue
        if k == "tool_use":
            n_tool += 1
            name = ev.get("name", "?")
            inp = ev.get("input") or {}
            fp = inp.get("file_path") or inp.get("notebook_path")
            if fp:
                files[fp] += 1
                if name in ("Write", "Edit", "NotebookEdit"):
                    wrote[fp] += 1
            elif name == "Bash" and inp.get("command"):
                cmds.append(clip(inp["command"], 130))
            elif name == "Agent" and inp.get("prompt"):
                asks.append(clip(inp.get("prompt"), 130))
        elif k == "thinking":
            continue        # 交接单里不留思考

    out = []
    A = out.append
    A("# 会话交接单 (ctx-guard digest)\n")
    A("- 源会话: `%s` (%.1f MB)" % (os.path.basename(path), os.path.getsize(path) / 1048576))
    A("- 时间跨度: %s → %s" % (first_ts, last_ts))
    A("- 工具调用 %d 次，其中改动文件 %d 个，命令 %d 条" % (n_tool, len(wrote), len(cmds)))
    A("- 生成时间: %s\n" % time.strftime("%Y-%m-%d %H:%M"))

    A("## 用户说过的话（按时间，最旧→最新）")
    if prompts:
        for t, p in prompts:
            A("- `%s` %s" % (t, p))
    else:
        A("- (无)")
    A("")

    A("## 改过的文件（这些是已落盘的既有事实，别重做）")
    if wrote:
        for f, c in wrote.most_common():
            A("- %s  ×%d" % (f, c))
    else:
        A("- (本次会话未写文件)")
    A("")

    A("## 读过的文件（重开会话时可优先看这些）")
    ro = [f for f, _ in files.most_common() if f not in wrote]
    for f in ro[:40]:
        A("- %s" % f)
    if len(ro) > 40:
        A("- …另 %d 个" % (len(ro) - 40))
    A("")

    if cmds:
        A("## 跑过的命令（去重，最近在后）")
        seen = []
        for c in cmds:
            if c not in seen:
                seen.append(c)
        for c in seen[-25:]:
            A("- `%s`" % c)
        A("")

    if asks:
        A("## 派给子代理的任务")
        for a in asks[-10:]:
            A("- %s" % a)
        A("")

    A("## 上次停在哪（最后几段助手正文）")
    for s in last_assistant[-4:]:
        A("- %s" % s)
    A("")

    text = "\n".join(out)
    if len(text.encode("utf-8")) > max_bytes:
        text = text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
        text += "\n\n…(已截到 %d KB，需要更多用 --max-bytes)" % (max_bytes // 1024)
    return text


def main():
    args = sys.argv[1:]
    max_bytes = 8192
    if "--max-bytes" in args:
        i = args.index("--max-bytes"); max_bytes = int(args[i + 1]); del args[i:i + 2]
    to_stdout = "--stdout" in args
    if to_stdout:
        args.remove("--stdout")
    target = next((a for a in args if not a.startswith("-")), None)
    pdir = project_dir()
    target = target or newest_transcript(pdir)
    if not target:
        print("找不到会话：%s" % pdir); return
    text = digest(target, max_bytes=max_bytes)
    if to_stdout:
        print(text); return
    outp = os.path.join(pdir, "CTX_DIGEST.md")
    with open(outp, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("已写出: %s  (%.1f KB, 源 %.1f MB -> 压缩 %.0f%%)"
          % (outp, len(text.encode('utf-8')) / 1024,
             os.path.getsize(target) / 1048576,
             100 * (1 - len(text.encode('utf-8')) / os.path.getsize(target))))


if __name__ == "__main__":
    main()
