#!/usr/bin/env python3
"""ctx-guard / statusline.py —— 用量看板。

既是状态栏（显示当前上下文占用 %），又是"用量看板"：把当前会话的
context_window 占用写到一个固定文件，供 gate.py / paste_split.py 读取，
从而能在"还剩多少"的基础上做拦截与提醒。

挂在 settings.json 的 "statusLine" 上。输入是 JSON（stdin），见
https://code.claude.com/docs/en/statusline ；本脚本只读其中 context_window /
model / cwd / session_id 等字段，缺字段也不崩。
"""
import sys, os, json, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import harness as _harness     # 适配器 + hook 输入大门
import config as _config       # 统一配置读取

H = None


def main():
    global H
    H = _harness.get_adapter()
    d = _harness.read_payload() or {}   # 没有输入也要照常打一行（与旧行为一致）

    cw = d.get("context_window") or {}
    used = cw.get("used_percentage")
    rem = cw.get("remaining_percentage")

    # 用量看板：写文件供其他 hook 读（含时间戳，过期的自动失效）
    try:
        model = d.get("model")
        with open(H.usage_file(), "w", encoding="utf-8") as fh:
            json.dump({"ts": time.time(),
                       "session_id": d.get("session_id"),
                       "used_percentage": used,
                       "remaining_percentage": rem,
                       "exceeds_200k": cw.get("exceeds_200k_tokens"),
                       "model": model.get("display_name") if isinstance(model, dict)
                       else model,
                       "cwd": d.get("cwd")}, fh, ensure_ascii=False)
    except Exception:
        pass

    # 自动重启看板（watchdog 消费）：每个会话一份，用 session_id 分档，
    # 避免"多个并发会话互相覆盖用量"——watchdog 只看当前那个。
    sid = d.get("session_id")
    if sid:
        try:
            d2 = H.sessions_dir()
            os.makedirs(d2, exist_ok=True)
            with open(os.path.join(d2, "%s.json" % sid), "w", encoding="utf-8") as fh:
                json.dump({"ts": time.time(), "session_id": sid,
                           "used_percentage": used, "remaining_percentage": rem,
                           "cwd": d.get("cwd"),
                           "model": (model.get("display_name")
                                     if isinstance(model, dict) else model)}, fh,
                          ensure_ascii=False)
        except Exception:
            pass

    # 状态栏一行
    cwd = d.get("cwd") or ""
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home):]
    base = os.path.basename(cwd.rstrip("/\\")) or cwd

    model = d.get("model")
    if isinstance(model, dict):
        model = model.get("display_name") or model.get("id") or ""
    model = str(model or "")
    if len(model) > 22:
        model = model[:22]

    cost = d.get("cost")
    cost_s = ""
    if isinstance(cost, dict) and cost.get("total_cost_usd") is not None:
        cost_s = " $%.2f" % cost["total_cost_usd"]

    if isinstance(used, (int, float)):
        mark = "[!]" if used >= 80 else ("[~]" if used >= 60 else "[ok]")
        ctx_s = "%s ctx %.0f%%" % (mark, used)
    else:
        ctx_s = "ctx ?"

    print("%s  %s  %s%s" % (ctx_s, model or "?", base, cost_s))


if __name__ == "__main__":
    main()
