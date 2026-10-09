#!/usr/bin/env python3
"""ctx-guard / tokens.py —— 唯一的 token 估算实现（原先在 scan/split/paste_split 里各有一份）。

这些常数是**面向 Claude 系列模型**标定的经验值，换模型族未必成立；因此它们集中在这里，
将来接入别的模型族时只需改这一处（或由 harness 适配器覆盖）。

  ASCII_DIV = 3.6   英文/代码约 3.6 字节/token
  CJK_MUL   = 1.15  中文约 1.15 token/字
  IMG_MAX   = 1600  单图 token 封顶（长边约 1568px 时的实测上限）

纯函数、无副作用、不读环境变量 —— 方便单测。
"""
import base64

ASCII_DIV = 3.6
CJK_MUL = 1.15
IMG_MAX = 1600


def est_tok(s) -> int:
    """按 ASCII/CJK 混排粗略估 token 数。"""
    if not s:
        return 0
    s = str(s)
    a = sum(1 for ch in s if ord(ch) < 128)
    return int(a / ASCII_DIV + (len(s) - a) * CJK_MUL)


def img_dims(b64: str):
    """从 base64 图像头里读宽高（PNG / JPEG）。读不到返回 None。"""
    try:
        raw = base64.b64decode(b64[:6000], validate=False)
    except Exception:
        return None
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
        w = int.from_bytes(raw[16:20], "big")
        h = int.from_bytes(raw[20:24], "big")
        return (w, h) if 0 < w < 100000 and 0 < h < 100000 else None
    if raw[:2] == b"\xff\xd8":                      # JPEG: 找 SOF 段
        i = 2
        while i + 9 < len(raw):
            if raw[i] != 0xFF:
                i += 1
                continue
            m = raw[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                     0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and i + 9 < len(raw):
                h = int.from_bytes(raw[i + 5:i + 7], "big")
                w = int.from_bytes(raw[i + 7:i + 9], "big")
                return (w, h)
            if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            seg = int.from_bytes(raw[i + 2:i + 4], "big")
            i += 2 + seg
        return None
    return None


def img_tok(b64len: int, dims) -> int:
    """图片 token 数：有尺寸就按 w·h/750，否则按 base64 长度的常见压缩率粗估。"""
    if dims:
        return min(IMG_MAX, max(1, round(dims[0] * dims[1] / 750)))
    approx_px = (b64len * 3 // 4) * 8
    return min(IMG_MAX, max(1, round(approx_px / 750)))
