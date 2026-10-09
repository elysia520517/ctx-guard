#!/usr/bin/env python3
"""ctx-guard / split.py —— 把过长的文本/文件切成小块，供"分段执行任务"。

用途：用户粘进来/给出的大文本（日志、某文档全文、一批 CSV、超大 diff、
一堆待办…）不该整块进主上下文。先落到盘上，切块，然后**每块喂一个子代理
只回结论** —— 原文永远不进主上下文，主上下文只累积各块的结论。

用法:
  python -I split.py big.txt                    # 默认按 ≤40 KB/块切到 big.txt.parts/
  python -I split.py big.txt --max-kb 20
  python -I split.py big.txt --chunks 8         # 指定块数（自动算每块大小）
  python -I split.py big.txt --overlap-lines 3  # 每块带上上块末尾几行，防切断记录
  python -I split.py - --out dir < big.txt      # 从 stdin 接一段粘贴
  python -I split.py big.txt --encoding gbk

输出：<out>/*.part-0001.txt … + <out>/MANIFEST.json（含每块行号范围、字节、估token）
      并在屏幕上打一份"分段执行模板"，可直接照着发子代理。

只读源文件，只写新的 part 目录，不碰原文件。
"""
import sys, os, json, argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ASCII_DIV = 3.6
CJK_MUL = 1.15


def est_tok(s: str) -> int:
    a = sum(1 for ch in s if ord(ch) < 128)
    return int(a / ASCII_DIV + (len(s) - a) * CJK_MUL)


def read_text(path, encoding):
    if path == "-":
        data = sys.stdin.buffer.read()
    else:
        with open(path, "rb") as fh:
            data = fh.read()
    enc = encoding or "utf-8"
    try:
        txt = data.decode(enc)
        replaced = 0
    except Exception:
        txt = data.decode(enc, errors="replace")
        replaced = txt.count("�")
    return txt, replaced


def split_lines(lines, max_bytes, overlap):
    """按字节上限聚行；overlap=每块额外重复上一块末尾 N 行。"""
    chunks, cur, cur_b, start = [], [], 0, 1
    for i, ln in enumerate(lines):
        b = len(ln.encode("utf-8", "ignore")) + 1
        if cur and cur_b + b > max_bytes:
            chunks.append((start, i, cur))          # 行号区间 [start, i]
            tail = cur[-overlap:] if overlap else []
            cur, cur_b = list(tail), sum(len(x.encode("utf-8", "ignore")) + 1 for x in tail)
            start = i - len(tail) + 1
        cur.append(ln)
        cur_b += b
    if cur:
        chunks.append((start, len(lines), cur))
    return chunks


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("src")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-kb", type=float, default=40.0)
    ap.add_argument("--chunks", type=int, default=0)
    ap.add_argument("--overlap-lines", type=int, default=0)
    ap.add_argument("--encoding", default=None)
    a = ap.parse_args()

    txt, replaced = read_text(a.src, a.encoding)
    total_b = len(txt.encode("utf-8", "ignore"))
    total_tok = est_tok(txt)
    lines = txt.splitlines() or [""]

    max_bytes = int(a.max_kb * 1024)
    if a.chunks and a.chunks > 1:
        max_bytes = max(1024, total_b // a.chunks)

    parts = split_lines(lines, max_bytes, a.overlap_lines)

    if a.src == "-":
        base = os.path.join(os.getcwd(), "paste")
    else:
        base = os.path.abspath(a.src)
    out = a.out or (base + ".parts")
    os.makedirs(out, exist_ok=True)

    manifest = {"src": a.src, "bytes": total_b, "est_tokens": total_tok,
                "lines": len(lines), "max_kb": a.max_kb, "chunks": []}
    print("=" * 76)
    print("源: %s   %.1f KB   ~%s tok   %d 行" %
          (a.src, total_b / 1024, format(total_tok, ","), len(lines)))
    if replaced:
        print("!! 解码出现 %d 个替换字符 —— 若源不是 UTF-8 请加 --encoding gbk 等" % replaced)
    print("切分为 %d 块 -> %s" % (len(parts), out))
    print("-" * 76)

    for idx, (l0, l1, ls) in enumerate(parts, 1):
        body = "\n".join(ls) + "\n"
        p = os.path.join(out, "part-%04d.txt" % idx)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(body)
        b = len(body.encode("utf-8", "ignore"))
        tk = est_tok(body)
        manifest["chunks"].append({"index": idx, "path": p, "bytes": b,
                                   "est_tokens": tk, "line_from": l0, "line_to": l1})
        print("  #%02d  %7.1f KB  ~%6s tok  L%d–L%d"
              % (idx, b / 1024, format(tk, ","), l0, l1))

    with open(os.path.join(out, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)

    print("-" * 76)
    print("清单: %s" % os.path.join(out, "MANIFEST.json"))
    print()
    print("分段执行模板（让我照做；每块一个子代理，原文不进主上下文）:")
    print('  对 #01..#%02d 每一块:' % len(parts))
    print('    Agent(prompt="任务: <一句话目标>。只读这一个文件: <块路径>。'
          '把结论压成 ≤10 条要点返回，不要贴原文，不要读其他块。")')
    print('  然后只把各块结论汇总。原文(=%.1f MB)全程留在 %s，不进上下文。'
          % (total_b / 1048576, out))
    print()


if __name__ == "__main__":
    main()
