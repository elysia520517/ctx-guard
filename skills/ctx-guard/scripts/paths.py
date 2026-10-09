#!/usr/bin/env python3
"""ctx-guard / paths.py —— 唯一的 transcript / 状态文件定位实现。

原先 scan/digest/resume/session_resume/watchdog 五个文件里各抄了一份 `project_dir()`
和 slug 规则；这里收成一处。**Slug 规则是 harness 专有的**（Claude Code 把 cwd 里
所有非字母数字字符替换成 `-`），所以具体规则由 harness 适配器提供；本模块只做缓存与
通用查找，并保留一个"没有适配器时"的向后兼容默认值。
"""
import os
import glob
import re
import time


def default_slug(cwd) -> str:
    """Claude Code 的项目目录名规则：所有非字母数字字符 → `-`。

    注意必须用 `re.sub` 而不是 replace 链 —— 逗号、空格、`:`、`\\`、`/` 全都算。
    例：`C:\\Users\\bronya\\_ctxtest` → `C--Users-bronya--ctxtest`。
    """
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def default_state_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude")


class Locator:
    """把"该去哪儿找会话"这件事收在一个对象里。

    harness 通过构造函数传入自己的 slug 规则与状态根目录；不传就退回 Claude Code 的约定，
    保证既有行为不变。
    """

    def __init__(self, state_dir=None, slug=None, project_subdir="projects",
                 transcript_ext=".jsonl"):
        self.state_dir = state_dir or default_state_dir()
        self._slug = slug or default_slug
        self.project_subdir = project_subdir
        self.transcript_ext = transcript_ext

    # ---- 目录 ----
    def slug(self, cwd) -> str:
        return self._slug(cwd or os.getcwd())

    @property
    def projects_root(self) -> str:
        return os.path.join(self.state_dir, self.project_subdir)

    def project_dir(self, cwd=None) -> str:
        return os.path.join(self.projects_root, self.slug(cwd))

    # ---- 会话文件 ----
    def transcripts(self, pdir):
        return glob.glob(os.path.join(pdir, "*" + self.transcript_ext))

    def newest_transcript(self, pdir, nth=0):
        files = sorted(self.transcripts(pdir), key=os.path.getmtime, reverse=True)
        return files[nth] if len(files) > nth else None

    def find_by_session(self, session_id):
        """按 session_id 在所有项目目录下找 transcript，取最新那个。"""
        hits = glob.glob(os.path.join(self.projects_root, "*",
                                      "%s%s" % (session_id, self.transcript_ext)))
        return max(hits, key=os.path.getmtime) if hits else None

    def stale(self, path, max_age_s) -> bool:
        """文件不存在、或比 max_age_s 更旧 → True。"""
        try:
            return (time.time() - os.path.getmtime(path)) > max_age_s
        except Exception:
            return True


_default = None


def default() -> Locator:
    """进程级默认 Locator（无适配器时的回退）。"""
    global _default
    if _default is None:
        _default = Locator()
    return _default
