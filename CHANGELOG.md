# 更新日志

## v0.1.0

首个版本。

- `split.py` / `paste_split.py`：长粘贴自动落盘、切块，逐块交给子代理处理，原文不进入主上下文。
- `gate.py` / `hook_guard.py`：`PreToolUse` 守卫，拦截整读大文件与裸 `cat` 大文件。
- `resume.py` / `session_resume.py`：生成精简交接单，重启后自动注入。
- `statusline.py`：状态栏显示上下文占用，并产出按会话的用量文件。
- `watchdog.py` / `watchdog.cmd`：会话忙且接近上限时自动重启接力，带 `--max-restarts` 安全绳。
