#!/usr/bin/env python3
"""ctx-guard / scan.py —— 会话"重量"体检：找出是哪一步把上下文撑爆的。

用法（只读，不修改任何文件）:
  python -I scan.py                     # 扫当前目录所属项目的最新会话
  python -I scan.py <transcript.jsonl>  # 扫指定会话
  python -I scan.py --all               # 该项目所有会话按体积排名
  python -I scan.py --top 15            # 打印前 15 条最重的工具输出
  python -I scan.py --project <项目目录名>

按类别给出体积与 token 估算；并列出最重的 N 条工具输出（来自哪个工具、
哪个文件/命令）。图片按真实图像 token 计（w·h/750，上限 ~1600），
不按 base64 字节折算。
"""
import sys, os, json, re, glob, collections, base64

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import harness as _harness          # harness 探测 + 适配器
import tokens as _tokens            # token 估算（唯一实现，见 tokens.py）

# 兼容旧名字：外面若还在 import ASCII_DIV / est_tok / img_tok 也照旧可用
ASCII_DIV = _tokens.ASCII_DIV
CJK_MUL = _tokens.CJK_MUL
IMG_MAX = _tokens.IMG_MAX
est_tok = _tokens.est_tok
img_dims = _tokens.img_dims
img_tok = _tokens.img_tok


def project_dir(cwd=None):
    return _harness.locator().project_dir(cwd)


def newest_transcript(pdir):
    return _harness.locator().newest_transcript(pdir)


def label_of(name, inp):
    if not isinstance(inp, dict):
        return name
    for k in ("file_path", "notebook_path", "path"):
        if inp.get(k):
            return "%s(%s)" % (name, os.path.basename(str(inp[k])))
    if inp.get("command"):
        return "%s(%s)" % (name, str(inp["command"]).strip().split("\n")[0][:60])
    if inp.get("pattern"):
        return "%s(/%s/)" % (name, str(inp["pattern"])[:40])
    return name


def trim(s):
    return " ".join(str(s).split())[:60]


def scan(path, top=10):
    cat_bytes = collections.Counter()
    cat_cnt = collections.Counter()
    cat_tok = collections.Counter()
    id2label = {}
    heavy = []                 # (bytes, tokens, label, preview)
    user_prompts = 0
    nlines = 0
    n_summaries = 0

    # 走适配器的归一化事件流：换 harness 只换适配器，这里的统计口径一个字都不动
    A = _harness.get_adapter()
    for ev in A.read_transcript(path):
        k = ev.get("kind")
        if k == "_stats":
            nlines = ev.get("lines", 0)
            break

        if k == "text":
            cls = "user" if ev.get("role") == "user" else "assistant"
            cat = "user_prompt(用户)" if cls == "user" else "assistant_text(正文)"
            txt = ev.get("text") or ""
            cat_bytes[cat] += len(txt.encode("utf-8", "ignore"))
            cat_cnt[cat] += 1
            cat_tok[cat] += est_tok(txt)
            if cls == "user":
                user_prompts += 1
                if ev.get("is_summary"):
                    n_summaries += 1
                    sub = "  └其中:auto-compact摘要"
                    cat_bytes[sub] += len(txt.encode("utf-8", "ignore"))
                    cat_cnt[sub] += 1

        elif k == "tool_use":
            id2label[ev.get("id")] = label_of(ev.get("name", "?"),
                                              ev.get("input") or {})
            cat_bytes["tool_use(调用)"] += ev.get("raw_len", 0)
            cat_cnt["tool_use(调用)"] += 1

        elif k == "tool_result":
            lab = id2label.get(ev.get("tool_use_id"), "?")
            nbytes = tok = 0
            kinds = set()
            preview = ""
            for b in ev.get("blocks") or ():
                bk = b[0] if isinstance(b, (tuple, list)) and b else None
                if bk == "image":
                    b64 = (b[1] if len(b) > 1 else "") or ""
                    nb = len(b64) * 3 // 4
                    if not nb:
                        nb = len(json.dumps(b[2] if len(b) > 2 else b).encode())
                    dims = img_dims(b64) if b64 else None
                    nbytes += nb
                    tok += img_tok(len(b64), dims)
                    kinds.add("image")
                    if not preview:
                        preview = "图片 %s" % ("%dx%d" % dims if dims else "(尺寸未知)")
                else:
                    txt = b[1] if len(b) > 1 else ""
                    if txt is None:
                        txt = json.dumps(b[2] if len(b) > 2 else b, ensure_ascii=False)
                    nbytes += len(txt.encode("utf-8", "ignore"))
                    tok += est_tok(txt)
                    kinds.add("text")
                    if not preview:
                        preview = trim(txt)
            kk = "+".join(sorted(kinds)) or "?"
            cat = "tool_result(%s)" % kk
            cat_bytes[cat] += nbytes
            cat_cnt[cat] += 1
            cat_tok[cat] += tok
            heavy.append((nbytes, tok, lab, preview))

        elif k == "thinking":
            txt = ev.get("text") or ""
            cat_bytes["thinking(思考)"] += len(txt.encode("utf-8", "ignore"))
            cat_cnt["thinking(思考)"] += 1
            cat_tok["thinking(思考)"] += est_tok(txt)

        elif k == "other":
            kk = "other:%s" % ev.get("subtype")
            cat_bytes[kk] += ev.get("raw_len", 0)
            cat_cnt[kk] += 1

    total = os.path.getsize(path)
    tot_tok = sum(cat_tok.values())
    print("=" * 82)
    print("会话: %s" % os.path.basename(path))
    print("体积: %.1f MB   行数: %d   用户发言: %d 条（含 auto-compact 摘要 %d 条）"
          % (total / 1048576, nlines, user_prompts, n_summaries))
    print("=" * 82)
    print("%-26s %9s %7s %12s %6s" % ("类别", "MB", "条数", "估token", "占比"))
    for k, v in cat_bytes.most_common(20):
        share = 100.0 * cat_tok.get(k, 0) / tot_tok if tot_tok else 0
        print("%-26s %9.2f %7d %12s %5.1f%%"
              % (k, v / 1048576, cat_cnt[k],
                 format(cat_tok.get(k, 0), ",") if cat_tok.get(k) else "-", share))
    print("-" * 82)
    print("最重的 %d 条工具输出（这才是撑爆上下文的真凶）:" % min(top, len(heavy)))
    for b, tk, lab, prev in sorted(heavy, reverse=True)[:top]:
        print("  %9.1f KB ~%8s tok  %-32s %s"
              % (b / 1024, format(tk, ","), lab[:32], prev[:36]))
    if heavy:
        tb = sum(b for b, _, _, _ in heavy)
        tt = sum(t for _, t, _, _ in heavy)
        topb = sum(b for b, _, _, _ in sorted(heavy, reverse=True)[:top])
        topt = sum(t for _, t, _, _ in sorted(heavy, reverse=True)[:top])
        print("-" * 82)
        print("工具输出合计 %.1f MB / ~%s tok（占全部 token 的 %.0f%%）；"
              "前 %d 条占了其中 %.0f%% 的 token"
              % (tb / 1048576, format(tt, ","), 100.0 * tt / tot_tok if tot_tok else 0,
                 min(top, len(heavy)), 100.0 * topt / tt if tt else 0))
    print()


def main():
    args = sys.argv[1:]
    top = 10
    if "--top" in args:
        i = args.index("--top"); top = int(args[i + 1]); del args[i:i + 2]
    pdir = None
    if "--project" in args:
        i = args.index("--project")
        pdir = os.path.join(_harness.get_adapter().locator().projects_root,
                            args[i + 1]); del args[i:i + 2]
    pdir = pdir or project_dir()

    if "--all" in args:
        files = sorted(_harness.locator().transcripts(pdir),
                       key=os.path.getsize, reverse=True)
        if not files:
            print("找不到会话：%s" % pdir); return
        print("项目 %s 的会话（按体积）:" % os.path.basename(pdir))
        for f in files[:25]:
            print("  %8.1f MB  %s" % (os.path.getsize(f) / 1048576, os.path.basename(f)))
        return

    target = next((a for a in args if not a.startswith("-")), None) or \
        newest_transcript(pdir)
    if not target:
        print("找不到会话：%s" % pdir); return
    scan(target, top=top)


if __name__ == "__main__":
    main()
