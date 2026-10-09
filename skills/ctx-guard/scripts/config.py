#!/usr/bin/env python3
"""ctx-guard / config.py —— 统一的配置读取（环境变量 + 可选配置文件）。

优先级（高 → 低）：
  1. 环境变量  CTXGUARD_<KEY>      （沿用既有名字，settings.json 的 env 里在用）
  2. 配置文件  <state_dir>/ctxguard.json 的顶层键    （整份缺失 = 忽略，纯 env 也能跑）
  3. 内置默认值

所有键都用**同一个名字**在 env 和 json 里出现，减少记忆负担：
  CTXGUARD_READ_KB    200    超过这个 KB 的文本文件禁止整读（gate.py）
  CTXGUARD_BLOCK      on     off → gate.py 退化为只提醒不拦
  CTXGUARD_PASTE_KB   12     触发自动切块的粘贴大小
  CTXGUARD_CHUNK_KB   40     split.py 的默认切块大小
  CTXGUARD_HARNESS    (空)   显式指定 harness 名；空 = 自动探测
  CTXGUARD_ADAPTERS   (空)   限制自动探测的候选集，逗号分隔；空 = 全部
  CTXGUARD_STATE_DIR  (空)   运行时状态根目录；空 = 该 harness 的默认值
  CTXGUARD_LANG      (空)    预留：提示语语言（zh/en）；空 = 跟随系统

配置文件格式就是一个扁平 JSON，键名省略 CTXGUARD_ 前缀：
  {"READ_KB": 300, "HARNESS": "claude"}
"""
import os
import json

DEFAULTS = {
    "READ_KB": 200.0,
    "BLOCK": "on",
    "PASTE_KB": 12.0,
    "CHUNK_KB": 40.0,
    "HARNESS": "",
    "ADAPTERS": "",
    "STATE_DIR": "",
    "LANG": "",
}

_ENV_PREFIX = "CTXGUARD_"
_cache = {"stamp": None, "file": {}}


def _config_path(state_dir=None):
    root = state_dir or os.environ.get(_ENV_PREFIX + "STATE_DIR") or \
        os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(root, "ctxguard.json")


def _load_file(state_dir=None) -> dict:
    """读配置文件；带 1 秒内存缓存，缺失/损坏一律返回空 dict。"""
    p = _config_path(state_dir)
    try:
        stamp = os.path.getmtime(p)
    except Exception:
        return {}
    if _cache["stamp"] == stamp:
        return _cache["file"]
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        data = data if isinstance(data, dict) else {}
    except Exception:
        data = {}
    _cache["stamp"] = stamp
    _cache["file"] = data
    return data


def get(key, default=None, state_dir=None):
    """取一个配置项：env > 文件 > 默认值。"""
    dflt = DEFAULTS.get(key, default)
    v = os.environ.get(_ENV_PREFIX + key)
    if v not in (None, ""):
        return v
    fv = _load_file(state_dir).get(key)
    if fv not in (None, ""):
        return fv
    return dflt


def number(key, default=None, state_dir=None) -> float:
    """取一个数值配置项；坏值退回默认。"""
    dflt = DEFAULTS.get(key, default)
    try:
        return float(get(key, dflt, state_dir))
    except (TypeError, ValueError):
        return float(dflt) if dflt is not None else 0.0


def flag(key, default=None, state_dir=None) -> bool:
    """取一个开关；只有显式的 'off'/'0'/'false'/'no' 算关。"""
    v = str(get(key, default, state_dir) or "").strip().lower()
    return v not in ("off", "0", "false", "no")


def listing(key, state_dir=None):
    """取一个逗号分隔的列表配置项。"""
    v = str(get(key, "", state_dir) or "")
    return [x.strip() for x in v.split(",") if x.strip()]
