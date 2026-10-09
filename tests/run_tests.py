#!/usr/bin/env python3
"""ctx-guard / tests/run_tests.py —— 内置自测（只用标准库 unittest）。

运行（注意 -I 与本仓库"脚本必须隔离运行"的约定一致）：

    python -I tests/run_tests.py

覆盖：
  · tokens      —— est_tok / img_tok / img_dims 的边界
  · paths       —— 项目目录 slug 规则（最容易踩坑的那条）
  · config      —— env > 文件 > 默认 的优先级
  · adapters    —— 探测、能力降级、工具名映射
  · harness     —— hook 输入大门（空/垃圾/正常载荷）
  · hooks       —— 四个 hook 脚本在真实载荷下的**输出信封形状**（用子进程跑，端到端）
  · watchdog    —— decide() 纯函数（防"误复活"）
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "ctx-guard", "scripts")
sys.path.insert(0, SCRIPTS)

import adapter_base          # noqa: E402
import adapter_claude        # noqa: E402
import adapter_codex         # noqa: E402
import adapter_openai        # noqa: E402
import config                # noqa: E402
import harness               # noqa: E402
import paths                 # noqa: E402
import tokens                # noqa: E402
import watchdog              # noqa: E402


# --------------------------------------------------------------------- tokens
class TestTokens(unittest.TestCase):
    def test_est_tok_empty(self):
        self.assertEqual(tokens.est_tok(""), 0)
        self.assertEqual(tokens.est_tok(None), 0)

    def test_est_tok_ascii(self):
        s = "a" * 360
        self.assertEqual(tokens.est_tok(s), 100)      # 360 / 3.6

    def test_est_tok_cjk(self):
        s = "中" * 100
        # 注意是 114 不是 115：1.15 的二进制表示略小于 1.15，100*1.15 = 114.999…
        # 取 int 截断成 114。这是**从旧脚本原样搬来**的行为，别"顺手修正"成 115。
        self.assertEqual(tokens.est_tok(s), 114)

    def test_est_tok_mixed(self):
        s = "abc中"
        self.assertEqual(tokens.est_tok(s), int(3 / 3.6 + 1 * 1.15))

    def test_img_tok_with_dims(self):
        self.assertEqual(tokens.img_tok(0, (750, 1000)), 1000)

    def test_img_tok_capped(self):
        self.assertEqual(tokens.img_tok(0, (100000, 100000)), tokens.IMG_MAX)

    def test_img_tok_unknown_dims_uses_len(self):
        v = tokens.img_tok(100000, None)
        self.assertTrue(1 <= v <= tokens.IMG_MAX)

    def test_img_dims_png(self):
        import struct, base64
        raw = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
               + struct.pack(">II", 640, 480) + b"\x00" * 20)
        self.assertEqual(tokens.img_dims(base64.b64encode(raw).decode()), (640, 480))

    def test_img_dims_garbage(self):
        import base64
        self.assertIsNone(tokens.img_dims(base64.b64encode(b"not an image").decode()))


# ---------------------------------------------------------------------- paths
class TestPaths(unittest.TestCase):
    def test_slug_matches_claude_rule(self):
        r"""逗号/空格/冒号/反斜杠都算 '-'，这是最容易写错的一条。"""
        self.assertEqual(paths.default_slug(r"C:\Users\bronya\_ctxtest"),
                         "C--Users-bronya--ctxtest")
        self.assertEqual(paths.default_slug(r"C:\a b,c"),
                         "C--a-b-c")
        self.assertEqual(paths.default_slug("/home/x/proj"),
                         "-home-x-proj")

    def test_project_dir_under_state(self):
        loc = paths.Locator(state_dir="/tmp/sd")
        self.assertEqual(loc.project_dir("/a/b"),
                         os.path.join("/tmp/sd", "projects", "-a-b"))

    def test_newest_transcript_and_find(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "projects", "-a"))
            pdir = os.path.join(td, "projects", "-a")
            for n in ("x.jsonl", "y.jsonl"):
                with open(os.path.join(pdir, n), "w") as fh:
                    fh.write("{}\n")
            os.utime(os.path.join(pdir, "x.jsonl"), (1000, 1000))
            loc = paths.Locator(state_dir=td)
            self.assertTrue(loc.newest_transcript(pdir).endswith("y.jsonl"))
            self.assertTrue(loc.newest_transcript(pdir, 1).endswith("x.jsonl"))
            self.assertIsNone(loc.newest_transcript(pdir, 9))
            self.assertTrue(loc.find_by_session("x").endswith("x.jsonl"))
            self.assertIsNone(loc.find_by_session("zzz"))

    def test_stale(self):
        loc = paths.Locator()
        self.assertTrue(loc.stale("/definitely/not/here", 10))
        with tempfile.NamedTemporaryFile() as fh:
            self.assertFalse(loc.stale(fh.name, 3600))
            self.assertTrue(loc.stale(fh.name, -1))


# --------------------------------------------------------------------- config
class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name
        config._cache["stamp"] = None

    def tearDown(self):
        for k in ("READ_KB", "BLOCK", "HARNESS", "ADAPTERS"):
            os.environ.pop("CTXGUARD_" + k, None)
        self.tmp.cleanup()

    def test_default(self):
        self.assertEqual(config.number("READ_KB", state_dir=self.state), 200.0)

    def test_file_then_env(self):
        with open(os.path.join(self.state, "ctxguard.json"), "w") as fh:
            json.dump({"READ_KB": 300}, fh)
        config._cache["stamp"] = None
        self.assertEqual(config.number("READ_KB", state_dir=self.state), 300.0)
        os.environ["CTXGUARD_READ_KB"] = "123"
        self.assertEqual(config.number("READ_KB", state_dir=self.state), 123.0)

    def test_flag(self):
        for off in ("off", "0", "false", "no", "OFF"):
            os.environ["CTXGUARD_BLOCK"] = off
            self.assertFalse(config.flag("BLOCK", state_dir=self.state), off)
        os.environ["CTXGUARD_BLOCK"] = "on"
        self.assertTrue(config.flag("BLOCK", state_dir=self.state))

    def test_listing(self):
        os.environ["CTXGUARD_ADAPTERS"] = "claude, codex ,"
        self.assertEqual(config.listing("ADAPTERS", state_dir=self.state),
                         ["claude", "codex"])

    def test_bad_number_falls_back(self):
        os.environ["CTXGUARD_READ_KB"] = "banana"
        self.assertEqual(config.number("READ_KB", state_dir=self.state), 200.0)


# ------------------------------------------------------------------- adapters
class TestAdapters(unittest.TestCase):
    def test_capability_detection(self):
        a = adapter_claude.ClaudeAdapter()
        self.assertTrue(adapter_base.supports(a, "read_transcript"))
        self.assertTrue(adapter_base.supports(a, "block_tool"))
        base = adapter_base.Adapter()
        self.assertFalse(adapter_base.supports(base, "read_transcript"))
        self.assertFalse(adapter_base.supports(base, "block_tool"))

    def test_base_degrades_safely(self):
        """基类必须"什么都不炸"：返回空事件流、None 用量、放行、不支持起会话。"""
        b = adapter_base.Adapter()
        self.assertEqual(list(b.read_transcript("whatever")), [])
        self.assertIsNone(b.context_usage())
        self.assertIsNone(b.sessions_map())
        self.assertIsNone(b.session_status("x"))
        self.assertEqual(b.block_tool(None, "x"), 0)
        self.assertFalse(b.can_block())
        self.assertEqual(b.launch_continue(".", "x")[0], "unsupported")
        self.assertEqual(b.empty_reply(), "")

    def test_tool_alias_mapping(self):
        a = adapter_claude.ClaudeAdapter()
        self.assertEqual(a.canonical_tool("Task"), "Agent")     # 旧名 → 规范名
        self.assertEqual(a.canonical_tool("Read"), "Read")
        self.assertEqual(a.canonical_tool("Weird"), "Weird")

    def test_codex_detect_and_locator(self):
        c = adapter_codex.CodexAdapter()
        os.environ.pop("CODEX_HOME", None)
        self.assertFalse(c.detect())              # 本机没有 CODEX* 指纹、也没有 ~/.codex
        os.environ["CODEX_HOME_FAKE"] = "1"
        try:
            self.assertTrue(c.detect())
        finally:
            os.environ.pop("CODEX_HOME_FAKE", None)
        self.assertTrue(c.state_dir().endswith(".codex"))

    def test_codex_home_override(self):
        os.environ["CODEX_HOME"] = r"C:\tmp\codexhome"
        try:
            c = adapter_codex.CodexAdapter()
            self.assertEqual(c.state_dir(), r"C:\tmp\codexhome")
            self.assertEqual(c.locator().projects_root,
                             os.path.join(r"C:\tmp\codexhome", "sessions"))
        finally:
            os.environ.pop("CODEX_HOME", None)

    def test_codex_newest_rollout(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "sessions", "2026", "10", "09")
            os.makedirs(d)
            old = os.path.join(d, "rollout-1-a.jsonl")
            new = os.path.join(d, "rollout-2-b.jsonl")
            for p in (old, new):
                open(p, "w").close()
            os.utime(old, (1000, 1000))
            os.environ["CODEX_HOME"] = td
            try:
                self.assertEqual(adapter_codex.CodexAdapter().newest_rollout(), new)
            finally:
                os.environ.pop("CODEX_HOME", None)
        # 什么都没有时返回 None，不抛
        os.environ["CODEX_HOME"] = tempfile.gettempdir() + "/__no_such_codex__"
        try:
            self.assertIsNone(adapter_codex.CodexAdapter().newest_rollout())
        finally:
            os.environ.pop("CODEX_HOME", None)

    def test_openai_adapter_shape(self):
        o = adapter_openai.OpenAIAdapter()
        self.assertIn(o.name, ("openai",))
        self.assertTrue(o.state_dir().endswith(os.path.join(".ctxguard", "openai")))

    def test_openai_duck_hooks_installs_only_available(self):
        o = adapter_openai.OpenAIAdapter()
        seen = []
        h = o.make_hooks(on_usage=seen.append)
        self.assertTrue(hasattr(h, "installed_methods"))
        names = h.installed_methods()
        self.assertTrue(all(n.startswith("on_") for n in names))

    def test_openai_usage_tracker_tolerates_missing_fields(self):
        class U:
            input_tokens = 7
        t = adapter_openai._UsageTracker()
        t.add(type("R", (), {"usage": U()})())
        self.assertEqual(t.as_usage()["input_tokens"], 7)
        t.add(None)                       # 不能炸
        self.assertEqual(t.calls, 2)

    def test_openai_block_raises(self):
        o = adapter_openai.OpenAIAdapter()
        with self.assertRaises(adapter_openai.OpenAIBlocked):
            o.block_tool(None, "boom")


# -------------------------------------------------------------------- harness
class TestHarness(unittest.TestCase):
    def test_registry_and_fallback(self):
        self.assertEqual(harness.get_adapter(force="claude").name, "claude")

    def test_unknown_harness_falls_back(self):
        a = harness.get_adapter(force="does-not-exist")
        self.assertEqual(a.name, harness.default_adapter_name())

    def test_looks_like_hook(self):
        self.assertTrue(harness.looks_like_hook({"tool_name": "Read"}))
        self.assertTrue(harness.looks_like_hook({"prompt": "hi"}))
        self.assertFalse(harness.looks_like_hook({"totally": "unrelated"}))
        self.assertFalse(harness.looks_like_hook(None))

    def test_read_payload_none_without_stdin(self):
        self.assertIsNone(harness.read_payload())

    def test_describe_shape(self):
        d = harness.describe()
        self.assertIn("harness", d)
        self.assertIn("read_transcript", d["capabilities"])


# ---------------------------------------------------------------- hooks (e2e)
def run_hook(script, payload, extra_env=None):
    env = dict(os.environ)
    env.update(extra_env or {})
    data = (json.dumps(payload).encode("utf-8") if payload is not None else b"")
    p = subprocess.run([sys.executable, "-I", os.path.join(SCRIPTS, script)],
                       input=data, capture_output=True, env=env, timeout=60,
                       cwd=tempfile.gettempdir())
    return p.returncode, p.stdout.decode("utf-8", "replace").strip(), \
        p.stderr.decode("utf-8", "replace").strip()


class TestHooksEndToEnd(unittest.TestCase):
    def test_no_stdin_is_silent_noop(self):
        for s in ("gate.py", "hook_guard.py", "paste_split.py", "session_resume.py"):
            rc, out, err = run_hook(s, None)
            self.assertEqual(rc, 0, s)
            self.assertEqual(out, "", s)
            self.assertEqual(err, "", s)

    def test_unknown_payload_is_noop(self):
        for s in ("gate.py", "hook_guard.py", "paste_split.py", "session_resume.py"):
            rc, out, err = run_hook(s, {"totally": "unrelated"})
            self.assertEqual((rc, out, err), (0, "", ""), s)

    def test_gate_blocks_big_read(self):
        with tempfile.TemporaryDirectory() as td:
            big = os.path.join(td, "big.txt")
            with open(big, "w") as fh:
                fh.write("x" * 400000)
            rc, out, err = run_hook("gate.py", {
                "hook_event_name": "PreToolUse", "tool_name": "Read",
                "tool_input": {"file_path": big}, "cwd": td})
            self.assertEqual(rc, 2)
            self.assertIn("[ctx-guard]", err)

    def test_gate_allows_when_block_off(self):
        with tempfile.TemporaryDirectory() as td:
            big = os.path.join(td, "big.txt")
            with open(big, "w") as fh:
                fh.write("x" * 400000)
            rc, out, err = run_hook("gate.py", {
                "hook_event_name": "PreToolUse", "tool_name": "Read",
                "tool_input": {"file_path": big}, "cwd": td},
                extra_env={"CTXGUARD_BLOCK": "off"})
            self.assertEqual(rc, 0)
            body = json.loads(out)
            self.assertEqual(body["hookSpecificOutput"]["hookEventName"], "PreToolUse")
            self.assertIn("offset", body["hookSpecificOutput"]["additionalContext"])

    def test_gate_ignores_small_read_and_images(self):
        with tempfile.TemporaryDirectory() as td:
            small = os.path.join(td, "s.txt")
            with open(small, "w") as fh:
                fh.write("hi")
            rc, out, err = run_hook("gate.py", {
                "hook_event_name": "PreToolUse", "tool_name": "Read",
                "tool_input": {"file_path": small}, "cwd": td})
            self.assertEqual((rc, out, err), (0, "", ""))
            rc, out, err = run_hook("gate.py", {
                "hook_event_name": "PreToolUse", "tool_name": "Read",
                "tool_input": {"file_path": os.path.join(td, "x.png")}, "cwd": td})
            self.assertEqual((rc, out, err), (0, "", ""))

    def test_paste_split_stages_long_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            rc, out, err = run_hook("paste_split.py", {
                "hook_event_name": "UserPromptSubmit",
                "prompt": "line\n" * 6000, "cwd": td},
                extra_env={"CTXGUARD_PASTE_KB": "8", "CTXGUARD_CHUNK_KB": "4"})
            self.assertEqual(rc, 0)
            body = json.loads(out)
            self.assertEqual(body["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
            self.assertIn("已落盘", body["hookSpecificOutput"]["additionalContext"])
            self.assertTrue(os.path.isdir(os.path.join(td, ".ctxguard-paste")))

    def test_paste_split_ignores_short_prompt(self):
        rc, out, err = run_hook("paste_split.py", {
            "hook_event_name": "UserPromptSubmit", "prompt": "hi",
            "cwd": tempfile.gettempdir()})
        self.assertEqual((rc, out, err), (0, "", ""))


# ------------------------------------------------------------------ watchdog
class TestWatchdogDecide(unittest.TestCase):
    CASES = [
        # (rec_live, status, usage, last_status, thr, expect_do)
        (False, None, None, "busy", 82, True),     # 忙到一半没了 → 接
        (False, None, None, "idle", 82, False),    # 正常收工 → 不接
        (False, None, None, None, 82, False),
        (True, "busy", 90, None, 82, True),        # 边干边满 → 接
        (True, "idle", 95, "busy", 82, False),     # 闲着的高占用 → 不接（防误复活）
        (True, "busy", 50, "busy", 82, False),
        (True, "busy", 82, "busy", 82, True),      # 正好等于阈值 → 接
    ]

    def test_decide(self):
        for rec_live, status, usage, last_status, thr, want in self.CASES:
            do, why = watchdog.decide(rec_live, status, usage, last_status, thr)
            self.assertEqual(do, want,
                             "case %r -> %r (%s)" % ((rec_live, status, usage,
                                                      last_status, thr), do, why))

    def test_decide_reason_only_when_not_acting(self):
        do, why = watchdog.decide(True, "idle", 95, "busy", 82)
        self.assertFalse(do)
        self.assertTrue(why and why.startswith("非 busy"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
