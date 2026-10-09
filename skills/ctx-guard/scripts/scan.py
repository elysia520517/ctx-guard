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

ASCII_DIV = 3.6      # 英文/代码 ~3.6 字节/token
CJK_MUL = 1.15       # 中文 ~1.15 token/字
IMG_MAX = 1600       # Claude 单图 token 上限（长边 1568px）


def est_tok(s: str) -> int:
    a = sum(1 for ch in s if ord(ch) < 128)
    return int(a / ASCII_DIV + (len(s) - a) * CJK_MUL)


def img_dims(b64: str):
    """从 base64 图像头里读宽高（PNG / JPEG）。读不到返回 None。"""
    try:
        raw = base64.b64decode(b64[:6000], validate=False)
    except Exception:
        return None
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
        w = int.from_bytes(raw[16:20], "big")
        h = int.from_bytes(raw[20:24], "big")
        return (w, h) if 0 < w < 100000 and 0 < h < 100000 else None
    if raw[:2] == b"\xff\xd8":                      # JPEG: 找 SOF 段
        i = 2
        while i + 9 < len(raw):
            if raw[i] != 0xFF:
                i += 1; continue
            m = raw[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                     0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and i + 9 < len(raw):
                h = int.from_bytes(raw[i + 5:i + 7], "big")
                w = int.from_bytes(raw[i + 7:i + 9], "big")
                return (w, h)
            if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                i += 2; continue
            seg = int.from_bytes(raw[i + 2:i + 4], "big")
            i += 2 + seg
        return None
    return None


def img_tok(b64len: int, dims):
    if dims:
        return min(IMG_MAX, max(1, round(dims[0] * dims[1] / 750)))
    # 未知尺寸时，按 base64 长度的常见压缩率粗估像素
    approx_px = (b64len * 3 // 4) * 8
    return min(IMG_MAX, max(1, round(approx_px / 750)))


def project_dir(cwd=None):
    cwd = cwd or os.getcwd()
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)   # Claude 的项目目录名规则：所有非字母数字 -> "-"
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", slug)


def newest_transcript(pdir):
    files = glob.glob(os.path.join(pdir, "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


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

    for line in open(path, "rb"):
        nlines += 1
        try:
            d = json.loads(line)
        except Exception:
            continue
        msg = d.get("message") or {}
        content = msg.get("content")
        role = msg.get("role") or d.get("type")

        if isinstance(content, str):
            cat = "user_prompt(用户)" if role == "user" else "assistant_text(正文)"
            cat_bytes[cat] += len(content.encode("utf-8", "ignore"))
            cat_cnt[cat] += 1
            cat_tok[cat] += est_tok(content)
            if role == "user":
                user_prompts += 1
                if content.startswith("This session is being continued"):
                    n_summaries += 1
                    cat = "  └其中:auto-compact摘要"
                    cat_bytes[cat] += len(content.encode("utf-8", "ignore"))
                    cat_cnt[cat] += 1
            continue
        if not isinstance(content, list):
            continue

        for blk in content:
            if not isinstance(blk, dict):
                continue
            t = blk.get("type")
            if t == "tool_use":
                id2label[blk.get("id")] = label_of(blk.get("name", "?"),
                                                    blk.get("input") or {})
                cat_bytes["tool_use(调用)"] += len(
                    json.dumps(blk, ensure_ascii=False).encode("utf-8", "ignore"))
                cat_cnt["tool_use(调用)"] += 1
            elif t == "tool_result":
                lab = id2label.get(blk.get("tool_use_id"), "?")
                c = blk.get("content")
                blocks = c if isinstance(c, list) else [{"type": "text", "text": c}]
                nbytes = tok = 0
                kinds = set()
                preview = ""
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "image":
                        src = b.get("source") or {}
                        b64 = src.get("data", "") if isinstance(src, dict) else ""
                        nb = len(b64) * 3 // 4
                        if not nb:
                            nb = len(json.dumps(b).encode())
                        dims = img_dims(b64) if b64 else None
                        nbytes += nb
                        tok += img_tok(len(b64), dims)
                        kinds.add("image")
                        if not preview:
                            preview = "图片 %s" % (
                                "%dx%d" % dims if dims else "(尺寸未知)")
                    else:
                        txt = b.get("text") if isinstance(b, dict) else str(b)
                        if txt is None:
                            txt = json.dumps(b, ensure_ascii=False)
                        nbytes += len(txt.encode("utf-8", "ignore"))
                        tok += est_tok(txt)
                        kinds.add("text")
                        if not preview:
                            preview = trim(txt)
                kind = "+".join(sorted(kinds)) or "?"
                cat = "tool_result(%s)" % kind
                cat_bytes[cat] += nbytes
                cat_cnt[cat] += 1
                cat_tok[cat] += tok
                heavy.append((nbytes, tok, lab, preview))
            elif t == "thinking":
                txt = blk.get("thinking", "")
                cat_bytes["thinking(思考)"] += len(txt.encode("utf-8", "ignore"))
                cat_cnt["thinking(思考)"] += 1
                cat_tok["thinking(思考)"] += est_tok(txt)
            elif t == "text":
                txt = blk.get("text", "")
                cat_bytes["assistant_text(正文)"] += len(txt.encode("utf-8", "ignore"))
                cat_cnt["assistant_text(正文)"] += 1
                cat_tok["assistant_text(正文)"] += est_tok(txt)
            else:
                k = "other:%s" % t
                cat_bytes[k] += len(json.dumps(blk, ensure_ascii=False).encode())
                cat_cnt[k] += 1

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
        pdir = os.path.join(os.path.expanduser("~"), ".claude", "projects",
                            args[i + 1]); del args[i:i + 2]
    pdir = pdir or project_dir()

    if "--all" in args:
        files = sorted(glob.glob(os.path.join(pdir, "*.jsonl")),
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
