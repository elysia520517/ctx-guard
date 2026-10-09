#!/usr/bin/env python3
"""ctx-guard / session_resume.py —— SessionStart：自动注入上一会话的交接单。

上下文爆了以后重启，最烦的是"新会话什么都不记得"。这个 hook 在新会话启动时
找 `<项目目录>/RESUME_NEXT.md`（由 resume.py 写出），把它注入上下文，
然后**消费掉**（改名成 .consumed），避免每次开新会话都重复注入旧的。

挂 settings.json 的 hooks.SessionStart。过期（>24h）或不存在则安静退出。
"""
import sys, os, json, re, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import harness as _harness     # 适配器 + hook 输入大门

MAX_AGE = 24 * 3600


def project_dir(cwd):
    return _harness.locator().project_dir(cwd)


def main():
    d = _harness.any_stdin_payload()
    if d is None:
        return
    H = _harness.get_adapter()
    cwd = d.get("cwd") or os.getcwd()

    loc = _harness.locator()
    path = None
    for pdir in dict.fromkeys([project_dir(cwd), project_dir(os.getcwd())]):
        cand = os.path.join(pdir, "RESUME_NEXT.md")
        if os.path.exists(cand):
            path = cand
            break
    if not path or loc.stale(path, MAX_AGE):
        return

    try:
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
    except Exception:
        return
    body = body[:24000]

    msg = ("[ctx-guard] 检测到上一会话的交接单（上一轮上下文爆了/主动重启）。"
           "内容如下；**若你本次不是接着那个任务，直接忽略本段**。\n"
           "如果接着干：先按交接单确认已完成的部分（别重做），"
           "再从\"上次停在哪\"继续；需要时用 scan.py / split.py 收着读。\n"
           "---- 交接单开始 ----\n" + body + "\n---- 交接单结束 ----")

    try:
        os.replace(path, path + ".consumed")   # 消费掉，防重复注入
    except Exception:
        pass

    H.inject_context(d, msg, default_event="SessionStart")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
