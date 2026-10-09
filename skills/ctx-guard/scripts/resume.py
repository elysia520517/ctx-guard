#!/usr/bin/env python3
"""ctx-guard / resume.py —— 一键生成"重启接任务"的交接单。

上下文要爆了 / 已经爆了：在这里生成一份交接单，然后开新会话，新会话会自动
把它注入（见 session_resume.py），你只要说一句"继续"。

用法:
  python -I resume.py                # 交接**当前项目最新**会话
  python -I resume.py --prev         # 交接**上一个**会话（你已经重开、想接更早的那个时用）
  python -I resume.py <会话.jsonl>
  python -I resume.py --stdout       # 只打屏幕，不写文件
  python -I resume.py --max-bytes 12000

产物: <项目目录>/RESUME_NEXT.md   —— 新会话启动时被 SessionStart hook 消费一次。
      并在屏幕打出可直接粘贴的重启引导语。
"""
import sys, os, re, glob, time, argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import digest as dg            # 复用同一套抽取逻辑，单一事实来源
import harness as _harness     # harness 探测 + 适配器（含 project_dir / slug）


def project_dir(cwd=None):
    return _harness.locator().project_dir(cwd)


def newest(pdir, nth=0):
    return _harness.locator().newest_transcript(pdir, nth)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--prev", action="store_true")
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--max-bytes", type=int, default=10000)
    a = ap.parse_args()

    pdir = project_dir()
    target = a.path or newest(pdir, 1 if a.prev else 0)
    if not target:
        print("找不到会话：%s" % pdir)
        return

    text = dg.digest(target, max_bytes=a.max_bytes)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    header = ("<!-- ctx-guard resume | 源: %s | 生成: %s -->\n"
              % (os.path.basename(target), stamp))
    body = header + text

    if a.stdout:
        print(body)
        return

    outp = os.path.join(pdir, "RESUME_NEXT.md")
    os.makedirs(pdir, exist_ok=True)      # 新目录可能还没有项目目录
    with open(outp, "w", encoding="utf-8", newline="") as fh:
        fh.write(body)

    print("交接单已写出: %s" % outp)
    print()
    print("=" * 70)
    print("现在请：")
    print("  1. 直接开一个新会话（/clear 或在同目录重开 claude）")
    print("  2. 新会话启动时会**自动**注入这份交接单（SessionStart hook）")
    print("  3. 你只要说一句：继续")
    print("=" * 70)
    print("若新会话没自动接上，把下面这句连同 RESUME_NEXT.md 内容贴进去：")
    print()
    print("  「读 %s，据此继续未完成的任务；已落盘的改动别重做。」" % outp)
    print()


if __name__ == "__main__":
    main()
