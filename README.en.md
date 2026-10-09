# ctx-guard

**Keep long Claude Code sessions from blowing up the context window — automatically.**

<p align="center"><b>English</b> · <a href="README.md">中文</a></p>

`ctx-guard` is a [Claude Code](https://claude.com/claude-code) skill plus a small set of
hooks and a watchdog process. It does three things:

1. **Splits oversized input before it ever hits the context.**
   Paste a huge block of text → it's written to disk, chunked, and handed to subagents
   one chunk at a time. Only the subagents' *conclusions* enter your context; the raw
   text never does.
2. **Hard-blocks the classic context killers.**
   A `PreToolUse` hook refuses to whole-read a >200 KB text file or dump a huge file with
   a bare `cat`, and feeds a "use `offset`/`limit` / `| head`" hint back to the model.
3. **Auto-restarts when the window fills up.**
   A `SessionStart` hook re-injects a compact handoff on restart, and an optional
   `watchdog.py` process watches the session and — when it's **busy and nearly full** —
   writes a handoff and launches a fresh `claude --bg` session that continues the work.
   Zero manual action.

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

So this tool covers ~⅓ of the problem (input + tool output) that a skill *can* reach;
thinking and compaction are handled by restarting early and clearing often.

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
    └── watchdog.py       # auto-restart when a busy session nears the limit
```

All scripts are **read-only** w.r.t. your code; they only write under
`~/.claude/` and a `.ctxguard-paste/` folder in the current directory.

---

## Install

Clone this repo somewhere, then copy or symlink the skill into your Claude Code skills dir:

```bash
# macOS / Linux
cp -r skills/ctx-guard ~/.claude/skills/

# Windows (PowerShell)
Copy-Item -Recurse skills\ctx-guard "$env:USERPROFILE\.claude\skills\"
```

Then wire up the hooks in `~/.claude/settings.json` (merge, don't overwrite — keep your
existing `env`, etc.):

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
**Restart the session** for hooks to take effect.

> Always invoke the scripts with `python -I` — it isolates them from the current
> directory so a stray local `json.py`/`sitecustomize.py` can't hijack them.

---

## Usage

```bash
S=~/.claude/skills/ctx-guard/scripts

# health check: what's filling this session?
python -I $S/scan.py --top 12
python -I $S/scan.py --all                # rank all sessions by size

# chunk oversized text for staged execution
python -I $S/split.py big.txt --max-kb 40
python -I $S/split.py big.txt --chunks 8 --overlap-lines 3

# prepare a handoff, then restart and say "continue"
python -I $S/resume.py
```

### Full auto-restart

```bash
python -I $S/watchdog.py --dir "/path/to/your/project" --threshold 82 --poll 20 --max-restarts 10
# or on Windows: watchdog.cmd "C:\path\to\your\project"
```

The watchdog restarts **only** when the session is genuinely in trouble:
- the session is `busy` **and** usage ≥ threshold, or
- the session **ended while it was busy** (crashed / killed mid-task).

It does **not** restart an idle session sitting at high usage (that's just waiting for
you) — this prevents pointless "resurrections".

**Prerequisite:** the target directory must be **trusted** by Claude Code. Run `claude`
interactively there once and accept the trust prompt, or `claude --bg` will refuse to
start. The watchdog reports this and stops cleanly rather than spinning.

`--max-restarts` is the **safety rope** (default launcher uses 10); set it to cap spend.

**Scripts must run under `python -I` and each reconfigures stdout/stderr to UTF-8**
(Windows consoles default to GBK, which garbles the Chinese messages these scripts emit).

---

## Tuning (via `env` in settings.json)

| var | default | meaning |
|---|---|---|
| `CTXGUARD_READ_KB` | `200` | block whole reads of text files larger than this |
| `CTXGUARD_BLOCK` | `on` | `off` → `gate.py` becomes reminder-only |
| `CTXGUARD_PASTE_KB` | `12` | paste size that triggers auto-chunking |
| `CTXGUARD_CHUNK_KB` | `40` | chunk size for `split.py` |

Prefer the gentler `hook_guard.py` (never blocks, always `exit 0`) over `gate.py` if you
don't want interruptions.

---

## Notes & caveats

- Written and tested on **Windows 11** with Claude Code `2.1.295`; paths in hooks are
  Windows-style. Linux/macOS need the paths adjusted and `watchdog.cmd` replaced by a
  shell launcher.
- `gate.py`'s hard block relies on the documented `PreToolUse` exit-code-2 semantics
  (stderr fed back to the model). Verified via docs/contract; the block path was not
  reproduced end-to-end on this machine — see the code comments.
- Requires only the Python 3 standard library. No dependencies.

## License

MIT — see [LICENSE](LICENSE).
