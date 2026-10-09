#!/usr/bin/env python3
"""A/B 对比：改造前(git HEAD) 与改造后 的 hook 脚本，在相同 stdin 载荷下的行为。

只比对**可观测行为**：stdout、stderr、退出码。用来证明"Claude 侧零回退"。
用法： python -I abcompare.py <old_scripts_dir> <new_scripts_dir> <payloads_dir>
"""
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPTS = ["gate", "hook_guard", "paste_split", "session_resume"]


def run(script_path, payload_bytes, cwd):
    p = subprocess.run([sys.executable, "-I", script_path],
                       input=payload_bytes, capture_output=True, cwd=cwd)
    return (p.returncode,
            p.stdout.decode("utf-8", "replace").strip(),
            p.stderr.decode("utf-8", "replace").strip())


def main():
    old_dir, new_dir, pay_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    cwd = os.environ.get("CTXGUARD_AB_CWD") or os.getcwd()
    payloads = sorted(f for f in os.listdir(pay_dir) if f.endswith(".json"))
    bad = 0
    for s in SCRIPTS:
        for f in payloads:
            data = open(os.path.join(pay_dir, f), "rb").read()
            o = run(os.path.join(old_dir, s + ".py"), data, cwd)
            n = run(os.path.join(new_dir, s + ".py"), data, cwd)
            if o == n:
                print("  [ok]   %-15s < %s" % (s, f))
            else:
                bad += 1
                print("  [DIFF] %-15s < %s" % (s, f))
                print("         OLD exit=%s out=%r err=%r" % o)
                print("         NEW exit=%s out=%r err=%r" % n)
    # 额外：无 stdin（模拟被当模块 import / 没有 hook 输入）
    print("  --- 无 stdin ---")
    for s in SCRIPTS:
        o = subprocess.run([sys.executable, "-I", os.path.join(old_dir, s + ".py")],
                           stdin=subprocess.DEVNULL, capture_output=True, cwd=cwd)
        n = subprocess.run([sys.executable, "-I", os.path.join(new_dir, s + ".py")],
                           stdin=subprocess.DEVNULL, capture_output=True, cwd=cwd)
        ov = (o.returncode, o.stdout.decode("utf-8", "replace").strip(),
              o.stderr.decode("utf-8", "replace").strip())
        nv = (n.returncode, n.stdout.decode("utf-8", "replace").strip(),
              n.stderr.decode("utf-8", "replace").strip())
        print("  %-15s OLD=%s  NEW=%s %s" % (s, ov, nv, "" if ov == nv else "  <-- 不同(预期：新版多打 {} )"))
    print()
    print("有差异的载荷数（不含'无 stdin'那组）:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
