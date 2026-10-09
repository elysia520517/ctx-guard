# ctx-guard · 上下文卫士

**Keep long Claude Code sessions from blowing up the context window — automatically.**

<p align="center"><b>English</b> · <a href="README.md">中文</a></p>

`ctx-guard` is a [Claude Code](https://claude.com/claude-code) skill: one SKILL.md, a
handful of hooks, and a small watchdog process. It exists for one problem — you're deep
in a session, and the context window fills up.

It handles three things.

**1. Oversized input never reaches the context in the first place.**
Paste a wall of text and it lands on disk, gets chunked, and is handed to subagents one
chunk at a time. Only the subagents' *conclusions* enter your context — **the raw text
never does.**

**2. The classic context killers get hard-blocked.**
A `PreToolUse` hook refuses to whole-read a text file over 200 KB, and refuses to let a
bare `cat` dump a huge file into the window. It feeds a hint back to the model — "use
`offset`/`limit`, or pipe through `head`" — and lets it retry.

**3. When the window fills up, it restarts itself and keeps going.**
On restart, a `SessionStart` hook re-injects a compact handoff. An optional `watchdog.py`
sits in the background, and whenever it sees the session is **busy and nearly full**, it
writes a handoff and launches a fresh `claude --bg` session to carry on. **You don't lift
a finger.**

> **Not affiliated with Anthropic.** Built against Claude Code `2.1.295`.

---

## What actually fills the context (measured, not guessed)

From my own 85 MB session transcript, estimated token share:
| source | tokens | share |
|---|---:|---:|
| model **thinking** | 1.10 M | **37%** |
| user messages (77× auto-compact summaries) | 870 k | 30% |
| tool results — text (1451 calls) | 839 k | 28% |
| tool results — images (92) | 118 k | 4% |

Two counter-intuitive findings:
- **Images are not the main problem.** 18 MB of base64 ≈ 118 k tokens (each image is
  capped at ~1600 tokens; cost ≈ `w·h/750`). Smaller screenshots barely help.
- **Auto-compact summaries are a hidden sink.** That session was compacted 77 times;
  each ~34 KB summary enters history. The cure is **proactively `/clear`**, not waiting
  for auto-compact.

So this tool covers the ~⅓ of the problem a skill *can* reach (input + tool output).
Thinking and compaction are handled by restarting early and clearing often.

---

## Layout

```
skills/ctx-guard/
├── SKILL.md              # the skill: triggers + rules
├── watchdog.cmd          # Windows launcher for the auto-restart watchdog
└── scripts/
    ├── scan.py           # session "weight" health check (read-only)
    ├── digest.py         # giant session → ~8 KB handoff (read-only)
    ├── split.py          # chunk an oversized text/file for staged execution
    ├── resume.py         # write RESUME_NEXT.md for the next session
    ├── gate.py           # PreToolUse: block oversized reads / bare cat
    ├── hook_guard.py     # PreToolUse: gentler, reminder-only variant
    ├── paste_split.py    # UserPromptSubmit: long paste → disk + chunks + rules
    ├── session_resume.py # SessionStart: auto-inject the handoff on restart
    ├── statusline.py     # statusLine: show `ctx NN%`, write usage for the watchdog
    ├── watchdog.py       # auto-restart when a busy session nears the limit
    │
    ├── harness.py        # detect which tool we're under + the stdin gate
    ├── adapter_base.py   # adapter base class + the normalized event stream
    ├── adapter_claude.py # Claude Code adapter (default, most complete)
    ├── adapter_codex.py  # Codex CLI adapter (skeleton, unverified)
    ├── adapter_openai.py # OpenAI Agents SDK adapter (duck-typed)
    ├── config.py         # unified config (env + optional json)
    ├── paths.py          # the one project-dir / transcript lookup
    └── tokens.py         # the one token estimator
```

Every script is **read-only** with respect to your code. They only write under
`~/.claude/` and a `.ctxguard-paste/` folder in the current directory.

---

## Install

Clone the repo anywhere, then copy — or symlink — the skill into your Claude Code skills
directory:

```bash
# macOS / Linux
cp -r skills/ctx-guard ~/.claude/skills/

# Windows (PowerShell)
Copy-Item -Recurse skills\ctx-guard "$env:USERPROFILE\.claude\skills\"
```

Then wire the hooks into `~/.claude/settings.json`. **Merge** them in — don't overwrite the
existing `env` and friends:

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Read|Bash",
        "hooks": [{ "type": "command",
                    "command": "python -I ~/.claude/skills/ctx-guard/scripts/gate.py" }] }
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command",
                    "command": "python -I ~/.claude/skills/ctx-guard/scripts/paste_split.py" }] }
    ],
    "SessionStart": [
      { "hooks": [{ "type": "command",
                    "command": "python -I ~/.claude/skills/ctx-guard/scripts/session_resume.py" }] }
    ]
  },
  "statusLine": {
    "type": "command",
    "command": "python -I ~/.claude/skills/ctx-guard/scripts/statusline.py"
  }
}
```

On Windows, use absolute paths (e.g. `python -I C:/Users/you/.claude/skills/.../gate.py`).
**Restart the session** for the hooks to take effect.

> Always invoke scripts with `python -I`. It isolates them from the current directory, so a
> stray local `json.py` or `sitecustomize.py` can't get loaded first and hijack them.

---

## Usage

```bash
S=~/.claude/skills/ctx-guard/scripts

# health check: what's filling this session?
python -I $S/scan.py --top 12
python -I $S/scan.py --all                # rank all sessions in this project by size

# chunk oversized text for staged execution
python -I $S/split.py big.txt --max-kb 40
python -I $S/split.py big.txt --chunks 8 --overlap-lines 3

# write a handoff, then restart and say "continue"
python -I $S/resume.py
```

### Full auto-restart

```bash
python -I $S/watchdog.py --dir "/path/to/your/project" --threshold 82 --poll 20 --max-restarts 10
# or on Windows: watchdog.cmd "C:\path\to\your\project"
```

The watchdog only steps in when a session is genuinely in trouble:

- it's `busy` **and** usage is at or past the threshold, or
- it **ended while it was busy** (crashed, or killed mid-task).

It will **not** restart an idle session that merely sits at high usage — that session is
just waiting on you — which is what keeps it from pointlessly "resurrecting" things.

**Prerequisite:** the target directory has to be **trusted** by Claude Code first. Run
`claude` there interactively once and accept the trust prompt; otherwise `claude --bg`
refuses to start. When that happens the watchdog says so plainly and exits cleanly, rather
than spinning in place.

`--max-restarts` is the **safety rope** (the launcher defaults to 10) — it caps auto-restarts
so spend can't run away.

**Scripts must run under `python -I`, and each one reconfigures stdout/stderr to UTF-8.**
Windows consoles default to GBK, which would garble the Chinese these scripts print.

---

## Tuning (via `env` in settings.json)

| var | default | meaning |
|---|---|---|
| `CTXGUARD_READ_KB` | `200` | block whole reads of text files larger than this |
| `CTXGUARD_BLOCK` | `on` | `off` → `gate.py` becomes reminder-only |
| `CTXGUARD_PASTE_KB` | `12` | paste size that triggers auto-chunking |
| `CTXGUARD_CHUNK_KB` | `40` | chunk size for `split.py` |
| `CTXGUARD_HARNESS` | empty | force a specific adapter; empty = auto-detect |
| `CTXGUARD_ADAPTERS` | empty | restrict auto-detect candidates, e.g. `claude,codex` |
| `CTXGUARD_STATE_DIR` | empty | runtime state root; empty = the tool's default |

They can also live in a flat JSON file (`~/.claude/ctxguard.json`, keys without the
`CTXGUARD_` prefix). The file is entirely optional — env vars alone work fine.

Prefer the gentler `hook_guard.py` (never blocks, always `exit 0`) over `gate.py` if you
don't want interruptions.

---

## Which tools it supports

"Switch tools, rewrite everything" is the ending this project most wanted to avoid. So the
environment facts — how a session is stored, how usage is reported, how a tool call is
blocked, how a new session is started — all live behind an adapter layer. The scripts only
ever talk to the adapter.

| capability | Claude Code | Codex CLI | OpenAI Agents SDK |
|---|---|---|---|
| identify itself (`detect`) | ✅ | ✅ env fingerprint | ✅ |
| transcript → normalized events | ✅ verified | ⛔ skeleton | ⛔ |
| read context usage | ✅ via statusline | ⛔ | ⚠️ per-call only |
| busy/idle session status | ✅ `agents --json` + file fallback | ⛔ | ⛔ |
| block a tool call | ✅ `exit 2` | ⛔ | ✅ raises |
| inject context | ✅ `hookSpecificOutput` | ⛔ | ✅ |
| launch a follow-up session | ✅ `claude --bg` | ⛔ | ⛔ |

⛔ doesn't mean "impossible" — it means **not implemented and not verified yet**. A missing
capability degrades that one feature (reminder-only, or you start the new session by hand);
it never takes the others down with it. That is what "capabilities are optional" buys.

> **Full disclosure:** neither `adapter_codex.py` nor `adapter_openai.py` has been run on a
> real install. The former's directory rules were read out of Codex's public source; the
> latter reflects over the SDK's hooks object by duck typing (that SDK is actively renaming
> its hook methods, so it deliberately imports no method names). Both files say "unverified"
> at the top and carry a to-do list. To add a tool, see
> [`docs/adding-a-harness.md`](docs/adding-a-harness.md).

### A renamed hook event can't kill the hooks

None of the four hook scripts calls `json.load(sys.stdin)` directly anymore. They all go
through one stdin gate: process the payload only if it carries a field we recognize,
otherwise emit an empty reply and exit. So **a renamed event, a renamed field, or the script
being imported as a module (no stdin at all)** can't crash or hang it — the worst case is it
quietly does nothing.

---

## Tests

Nothing to install (standard library only), no network needed:

```bash
python -I tests/run_tests.py          # 42 checks: tokens / path rules / adapters / 4 hooks e2e
python -I tests/abcompare.py <old_dir> <new_dir> <payload_dir>   # byte-for-byte A/B of old vs new
```

`abcompare.py` is the safety net for this refactor: feed the same set of real hook payloads
to the old and new scripts and compare the `(returncode, stdout, stderr)` triples. The
acceptance bar is **zero differences**.

---

## Notes & caveats

- Written and tested on **Windows 11** with Claude Code `2.1.295`; paths in hooks are
  Windows-style. Linux/macOS need the paths adjusted and `watchdog.cmd` replaced by a
  shell launcher.
- `gate.py`'s hard block relies on the documented `PreToolUse` exit-code-2 semantics
  (stderr fed back to the model). Verified via docs/contract; the block path was not
  reproduced end-to-end on this machine — see the code comments.
- Requires only the Python 3 standard library. No dependencies. (The adapter's reference to
  the OpenAI SDK is duck-typed: used only if you have it installed.)

## License

MIT — see [LICENSE](LICENSE).
