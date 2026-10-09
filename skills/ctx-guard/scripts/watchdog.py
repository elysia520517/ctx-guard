#!/usr/bin/env python3
"""ctx-guard / watchdog.py —— 全自动重启：上下文要爆就自动交接 + 起新会话继续。

零人工，但**只在会话确实在忙、且快满时**才自动重启；空闲等指令的会话不动。

判断依据（每 POLL 秒一轮）：
  · 用 statusline.py 写在 ~/.claude/ctx-guard-sessions/<sid>.json 的用量；
  · 用 `claude agents --json` 判断会话是否还活着、是 busy 还是 idle。
触发自动重启，当且仅当二者之一成立：
  A) 会话正 busy 且 used% >= threshold          —— 边干边满，自动接力
  B) 会话已结束，且它**结束前是 busy**            —— 干着干着爆了/被杀，自动续
不触发：会话 idle 时高占用（任务做完了在等你）、会话 idle 后结束（正常收工）。

用法:
  python -I watchdog.py --dir "C:\\path\\to\\your\\project"
  python -I watchdog.py --dir <dir> --threshold 82 --poll 20 --max-restarts 10
  python -I watchdog.py --dir <dir> --dry-run     # 只报告不启动
  python -I watchdog.py --dir <dir> --once        # 一轮就退（自检）

前提：目标目录必须已信任（在该目录交互式跑过一次 `claude` 并接受信任提示），
否则 `claude --bg` 会拒绝启动，watchdog 报错并停下（不空转、不刷屏）。
日志：<dir>/.ctxguard-watchdog.log
"""
import sys, os, re, json, time, glob, argparse, subprocess

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HOME = os.path.expanduser("~")
SESS_DIR = os.path.join(HOME, ".claude", "ctx-guard-sessions")
PROJ_ROOT = os.path.join(HOME, ".claude", "projects")
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
RESUME = os.path.join(SCRIPTS, "resume.py")
STATE = os.path.join(HOME, ".claude", "ctx-guard-watchdog.json")


def slug(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def norm(p):
    return os.path.normcase(os.path.abspath(p or ""))


def log(path, msg):
    line = "[%s] %s" % (time.strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def fresh_sessions(watch_dir):
    out = []
    for f in glob.glob(os.path.join(SESS_DIR, "*.json")):
        try:
            u = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if norm(u.get("cwd")) != norm(watch_dir):
            continue
        u["_age"] = time.time() - u.get("ts", 0)
        out.append(u)
    out.sort(key=lambda u: u.get("ts", 0), reverse=True)
    return out


def agents_map():
    """sessionId -> record；取不到返回 None（区别于空 dict）。"""
    try:
        p = subprocess.run(["claude", "agents", "--json"], capture_output=True,
                           text=True, timeout=30)
        arr = json.loads(p.stdout or "[]")
        return {s.get("sessionId"): s for s in arr if isinstance(s, dict)}
    except Exception:
        return None


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    try:
        json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def transcript(session_id):
    hits = glob.glob(os.path.join(PROJ_ROOT, "*", "%s.jsonl" % session_id))
    return max(hits, key=os.path.getmtime) if hits else None


def write_handoff(session_id, watch_dir):
    tr = transcript(session_id)
    if not tr:
        cand = glob.glob(os.path.join(PROJ_ROOT, slug(watch_dir), "*.jsonl"))
        tr = max(cand, key=os.path.getmtime) if cand else None
    if not tr:
        return None
    try:
        subprocess.run([sys.executable, "-I", RESUME, tr], cwd=watch_dir,
                       capture_output=True, timeout=60)
    except Exception:
        return None
    for d in dict.fromkeys([os.path.dirname(tr),
                            os.path.join(PROJ_ROOT, slug(watch_dir))]):
        p = os.path.join(d, "RESUME_NEXT.md")
        if os.path.exists(p):
            return p
    return None


def launch(watch_dir, model, dry):
    seed = ("接着上一个会话未完成的任务继续干。交接单见 RESUME_NEXT.md"
            "（若已自动注入则直接照它继续）：已完成、已落盘的改动不要重做，"
            "从\"上次停在哪\"续；要读大文件/长文本先切块再派子代理。")
    cmd = ["claude", "--bg"] + (["--model", model] if model else []) + [seed]
    if dry:
        return "(dry-run)"
    try:
        p = subprocess.run(cmd, cwd=watch_dir, capture_output=True, text=True,
                           timeout=180)
    except Exception as e:
        return "ERR:%s" % e
    out = (p.stdout or "") + (p.stderr or "")
    if "not trusted" in out.lower():
        return "UNTRUSTED"
    m = re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", out)
    return m[-1] if m else "LAUNCHED"


def decide(rec_live, status, usage, last_status, thr):
    """纯函数：返回 (是否重启, 原因)。"""
    if not rec_live:
        if last_status == "busy":
            return True, "会话结束（结束前在忙）"
        return False, None
    if isinstance(usage, (int, float)) and usage >= thr:
        if status == "busy":
            return True, "在工作且上下文 %.0f%% >= %.0f%%" % (usage, thr)
        return False, "非 busy（idle/未知）且占用 %.0f%%（不自动重启）" % usage
    return False, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--threshold", type=float, default=82.0)
    ap.add_argument("--poll", type=int, default=20)
    ap.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL") or None)
    ap.add_argument("--cooldown", type=int, default=60)
    ap.add_argument("--max-restarts", type=int, default=0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    watch_dir = os.path.abspath(a.dir)
    lg = os.path.join(watch_dir, ".ctxguard-watchdog.log")
    st = load_state()
    restarts = st.get("restarts", 0)
    watched = st.get("session_id")
    last_status = st.get("last_status")
    last_restart = 0.0
    warned = set()

    log(lg, "watchdog 启动 dir=%s threshold=%.0f%% poll=%ds max=%s%s"
        % (watch_dir, a.threshold, a.poll, a.max_restarts or "不限",
           " [dry-run]" if a.dry_run else ""))

    while True:
        try:
            now = time.time()
            sess = fresh_sessions(watch_dir)
            am = agents_map()

            if not watched and sess:
                pick = sess[0]
                if am:
                    pick = next((u for u in sess
                                 if u.get("session_id") in am), sess[0])
                watched = pick.get("session_id")
                st.update({"session_id": watched, "restarts": restarts})
                save_state(st)
                log(lg, "盯上会话 %s (ctx %.0f%%)"
                    % (str(watched)[:8], pick.get("used_percentage") or 0))

            usage = None
            for u in sess:
                if u.get("session_id") == watched:
                    usage = u.get("used_percentage")
                    break

            rec = am.get(watched) if (am and watched) else None
            rec_live = (rec is not None) if am is not None else bool(sess)
            status = (rec or {}).get("status")
            if rec_live and status:
                last_status = status
                st["last_status"] = status
                save_state(st)

            do, why = decide(rec_live, status, usage, last_status, a.threshold)
            if (not do) and why and why.startswith("非 busy") and watched not in warned:
                warned.add(watched)
                log(lg, why)

            if do:
                if a.max_restarts and restarts >= a.max_restarts:
                    log(lg, "已达重启上限 %d，停止自动重启。" % a.max_restarts)
                    return
                if now - last_restart >= a.cooldown or a.once:
                    log(lg, "触发自动重启：%s" % why)
                    hp = write_handoff(watched, watch_dir)
                    log(lg, "交接单: %s" % (hp or "(未生成)"))
                    sid = launch(watch_dir, a.model, a.dry_run)
                    if sid == "UNTRUSTED":
                        log(lg, "！！目标目录未被信任：请在 %s 里交互式跑一次 "
                               "`claude` 并接受信任提示。watchdog 停止。" % watch_dir)
                        return
                    restarts += 1
                    watched = None
                    last_status = None
                    last_restart = 0.0 if a.once else now
                    st.update({"session_id": None, "restarts": restarts,
                               "last_status": None})
                    save_state(st)
                    log(lg, "已重启 #%d，新会话 %s" % (restarts, str(sid)[:12]))
                    if a.dry_run:
                        log(lg, "(dry-run：未真正启动，本轮结束)")
                        return

            if a.once:
                return
            time.sleep(a.poll)

        except KeyboardInterrupt:
            log(lg, "收到中断，退出")
            return
        except Exception as e:
            log(lg, "循环异常（忽略并继续）：%r" % e)
            time.sleep(a.poll)


if __name__ == "__main__":
    main()
