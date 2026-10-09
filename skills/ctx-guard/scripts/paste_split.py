#!/usr/bin/env python3
"""ctx-guard / paste_split.py —— UserPromptSubmit：长粘贴自动落盘 + 切块 + 给分段指令。

用户一次粘进来一大段文本（或给出一个巨大文件），直接进主上下文会一次吃掉
好几万 token。这个 hook 在提示**提交时**：
  1. 把这段原文写到 <cwd>/.ctxguard-paste/<时间戳>.txt（原文不进主上下文）；
  2. 调 split.py 切成 ≤CHUNK_KB/块；
  3. 通过 additionalContext 告诉模型："原文已落盘，别整段读，按块派子代理"。

阈值：CTXGUARD_PASTE_KB 默认 12 KB（约 4k+ token 的粘贴就开始切）。

挂 settings.json 的 hooks.UserPromptSubmit。只读 stdin、只写新文件，不拦提交。
"""
import sys, os, json, re, time, subprocess, glob

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SPLIT = os.path.join(HERE, "split.py")
THRESH_KB = float(os.environ.get("CTXGUARD_PASTE_KB", "12") or 12)
CHUNK_KB = float(os.environ.get("CTXGUARD_CHUNK_KB", "40") or 40)
THRESH = int(THRESH_KB * 1024)

PREFIXES = ("This session is being continued", "[Image:", "<", "Caveat:")


def est_tok(s):
    a = sum(1 for ch in s if ord(ch) < 128)
    return int(a / 3.6 + (len(s) - a) * 1.15)


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    prompt = d.get("prompt")
    if not isinstance(prompt, str):
        return
    if len(prompt.encode("utf-8", "ignore")) < THRESH:
        return
    if any(prompt.startswith(p) for p in PREFIXES):
        return   # 系统注入的 / 摘要 / 图片引用，不是用户手粘

    cwd = d.get("cwd") or os.getcwd()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    ddir = os.path.join(cwd, ".ctxguard-paste")
    try:
        os.makedirs(ddir, exist_ok=True)
        src = os.path.join(ddir, "paste-%s.txt" % stamp)
        with open(src, "w", encoding="utf-8", newline="") as fh:
            fh.write(prompt)
    except Exception:
        return   # 写不了就静默放行，绝不打断

    nbytes = len(prompt.encode("utf-8", "ignore"))
    ntok = est_tok(prompt)

    parts = []
    out = None
    try:
        out = src + ".parts"
        subprocess.run([sys.executable, "-I", SPLIT, src, "--max-kb", str(CHUNK_KB),
                        "--out", out], capture_output=True, timeout=30)
        parts = sorted(glob.glob(os.path.join(out, "part-*.txt")))
    except Exception:
        pass

    msg = ("[ctx-guard] 你这次粘贴约 %.0f KB（~%s tok），已落盘，**不要再整段读它**。\n"
           "  原文: %s\n" % (nbytes / 1024, format(ntok, ","), src))
    if parts:
        msg += ("  已切成 %d 块: %s/part-0001.txt …\n"
                "  分段执行：每块派一个子代理，只回 ≤10 条结论，不要贴原文；"
                "最后只汇总各块结论。\n" % (len(parts), out))
    else:
        msg += "  切成块失败，请手动分块或用 Grep 定位需要的区间。\n"
    msg += "  若确实需要原文进上下文，请明确说\"整段读\"。"

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit", "additionalContext": msg}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
