# 加一个新 harness 适配器

`ctx-guard` 把"读哪个文件、怎么读、怎么拦、怎么起新会话"这些**环境事实**从脚本里抽了出来，
放进一个个适配器。脚本只通过 `H.<能力>()` 说话，从不直接碰任何 harness 的细节。

所以接一个新工具（Codex、Gemini CLI、opencode、Copilot……）本质上就三步：

1. 写一个 `scripts/adapter_<名字>.py`；
2. 在 `scripts/harness.py` 的 `REGISTRY` 里加一行；
3. 跑 `python -I -c "import harness; print(harness.describe())"` 看它认不认得出来。

**不用动任何既有脚本。** 一个都不要动。

---

## 一、能力是可选的，不是必填

这是整套设计里唯一需要牢记的一条：**能实现多少就实现多少**，剩下的让它降级。

各 harness 的差异极大，而且还在变（同一个 SDK 的 hook 方法名在不同版本里都改过）。所以基类
`Adapter` 里每个方法都有一个"安全降级"的默认实现——不报错、不做事、返回空。调用方一律先用
`adapter_base.supports(adapter, "能力名")` 问一句，没有就退化成"只提醒"或"跳过"。

最坏的情况（一个只实现了 `detect()` 的适配器）也能跑：所有 hook 都不拦不注，所有命令都还能用，
只是失去了自动护栏。

---

## 二、骨架

```python
#!/usr/bin/env python3
"""ctx-guard / adapter_<名字>.py —— <工具名> 适配器。"""
import os

import adapter_base
import paths


class MyAdapter(adapter_base.Adapter):
    name = "myharness"                      # 必须等于 REGISTRY 里的名字

    # 该工具里"读/搜/跑/派子代理"这类工具的名字 → ctx-guard 的规范名。
    # 找不到映射的按原样返回，所以不写也不会错，只是别名认不出来。
    tool_aliases = {
        "Read": ("read_file",), "Bash": ("shell", "run_command"),
        "Agent": ("task", "subagent"),
    }

    def detect(self) -> bool:
        """本进程是不是跑在这个 harness 下。拿不准就返回 False（宁可漏认，别乱认）。"""
        return os.path.isdir(os.path.join(os.path.expanduser("~"), ".myharness"))

    def state_dir(self) -> str:
        return os.path.join(os.path.expanduser("~"), ".myharness")

    def locator(self) -> paths.Locator:
        # 会话记录的目录规则。跟 Claude 一样"一个项目一个 slug 目录"就直接抄这行；
        # 如果是别的形状，覆写 Locator 的 projects_root / transcripts() / newest()。
        return paths.Locator(state_dir=self.state_dir(), slug=paths.default_slug)
```

就这些。这时候 `python -I -c "import harness; print(harness.describe())"` 已经能报出
`harness: myharness`，其余能力全部降级。

---

## 三、逐项能力：怎么实现，实现不了会怎样

按"收益 / 难度"排的，建议照着顺序补。

### `read_transcript(path)` —— 收益最大

产出一串**归一化事件**，`scan.py` 和 `digest.py` 只认这套字段：

```python
yield {"kind": "text", "role": "user"|"assistant", "text": str,
       "from": "string"|"block", "is_summary": bool, "ts": str}
yield {"kind": "thinking", "text": str, "ts": str}
yield {"kind": "tool_use", "id": str, "name": str, "input": dict,
       "raw_len": int, "ts": str}
yield {"kind": "tool_result", "tool_use_id": str,
       "blocks": [("text", 文本) | ("image", base64), ...], "ts": str}
yield {"kind": "other", "subtype": str, "raw_len": int, "ts": str}
yield {"kind": "_stats", "lines": int, "first_ts": str, "last_ts": str}   # 必须最后一条
```

`raw_len` 用**块序列化后的字节长度**（`json.dumps(blk, ensure_ascii=False)` 的 UTF-8 字节数），
别自己换算成 token——token 算法统一在 `tokens.py`，换 harness 不应该改变统计口径。

`_stats` 必须最后产出一次，`first_ts` / `last_ts` 要覆盖**所有**行（包括那些没产出事件的行）。

**不实现的话**：`scan.py` / `digest.py` 对所有会话都报空。

### `context_usage(payload=None, max_age_s=60)` —— 决定"还剩多少"

返回一个 dict，至少含 `used_percentage`；过期（比 `max_age_s` 老）就返回 `None`。
`gate.py` 靠它决定要不要在拦截信息后面附一句"上下文已占 NN%，这轮干完就 /clear"。

**不实现的话**：拦截照旧生效，只是少了那句用量提醒。

### `block_tool(payload, message)` —— 能不能真的拦住

返回**进程退出码**。Claude Code 的约定是 `2` = 阻塞，stderr 会回传给模型，所以 `message`
必须是 UTF-8 且写清"该怎么做"。不能拦的工具**返回 0**（放行）。

**不实现的话**：`gate.py` 退化成"只注入提醒、不拦"，不会报错。

### `inject_context(payload, text, default_event=None)` —— 怎么把话递进去

Claude Code 有 `hookSpecificOutput.additionalContext` 信封，所以它覆写这个方法打 JSON。
没有信封的 harness 用基类的默认实现就行——直接 `print(text)`。

`default_event` 是"载荷里没有 `hook_event_name` 时该用什么事件名"，`gate.py` 会传
`"PreToolUse"`，`session_resume.py` 会传 `"SessionStart"`。

### `sessions_map()` / `session_status(sid)` —— watchdog 用它判死活

`session_id -> 会话记录` 的映射，记录里要有 `status`（`"busy"` / `"idle"`）。
**取不到返回 `None`**——这一点很重要，`None`（"这个 harness 问不出来"）和空 dict
（"问出来了，当前没有会话"）在 watchdog 里走的是不同分支。

**不实现的话**：watchdog 只能靠用量文件的 mtime 判断，A 类触发（边干边满）会失效。

### `launch_continue(cwd, seed, model=None)` —— 自动接力

起一个新会话把活接着干。返回 `(状态, 说明)`，状态取值：

| 状态 | 含义 |
|---|---|
| `"launched"` | 起来了，说明里尽量带上新会话 id |
| `"untrusted"` | 目标目录没被信任 → watchdog 报错并**停下**（不空转、不刷屏） |
| `"unsupported"` | 本 harness 起不了 → watchdog 停下并让你手动开 |
| `"error"` | 其他失败 |

**不实现的话**：watchdog 走到这一步会停下并打印"当前 harness 不支持自动起会话，
交接单已写好，请手动开新会说'继续'"。

另外注意：`watchdog.py` 自己**不 import `json` 以外任何东西**来判断进程，所以一个
`launch_continue` 返回 `"unsupported"` 的适配器不会让 watchdog 崩，只是走降级分支。

### `empty_reply()` —— 无话可说时输出什么

默认 `""`（什么都不打）。Claude Code 的空 stdout 本身就是合法的 no-op；
如果你的 harness 要求"必须回一段 JSON 才算回应"，在这里返回 `"{}"`。

---

## 四、hook 输入大门：`any_stdin_payload()`

这是"兼容不同版本"的关键，也是新适配器**不需要**操心的一层。

4 个 hook 脚本都不再直接 `json.load(sys.stdin)`，而是调 `harness.any_stdin_payload()`：

1. 先看 stdin 是不是终端 / 有没有东西可读（Windows 上 `select()` 会因 WSAStartup 未初始化
   抛错，所以是"POSIX 用 select、Windows 用线程 + 超时"）；
2. 读到的 JSON 里**一个已知键都没有** → 返回 `None`（事件改名了 / 这不是喂给我们的）；
3. 是真的载荷 → 返回 dict。

拿到 `None` 时脚本就 `emit_empty()` 然后退出。于是：

- **事件名改了** → 读不到已知键 → 空回复，不炸；
- **被别的工具当模块 import（根本没有 stdin）** → 空回复，不炸；
- **正常情况** → 与今天逐字节一致。

已知键在 `harness.KNOWN_KEYS`。如果你的 harness 用的是完全另一套字段名（比如
`event_name` / `toolName`），**在这里加几个键**就行，不要改各个 hook 脚本——
大门是一处，脚本是四处，改一处总比改四处稳。

---

## 五、注册与探测顺序

```python
# harness.py
REGISTRY = (
    ("claude", "adapter_claude"),
    ("myharness", "adapter_myharness"),     # ← 加这一行
)
```

顺序即优先级：自动探测时**第一个 `detect()` 为真的胜出**。Claude 放最前是有意的——它是默认
且最完整的实现，放在最后当兜底。

用户还可以用环境变量干预：

- `CTXGUARD_HARNESS=myharness` —— 跳过自动探测，点名用哪个（点了不存在的会退回默认并打个警告）；
- `CTXGUARD_ADAPTERS=claude,myharness` —— 只在这几个里面自动探。

---

## 六、验收清单

新适配器最少要留下能跑的证据，不要"写完就算"：

- [ ] `python -I -c "import harness; print(harness.describe())"` 报出正确的 `harness` 名和
      非空的 `capabilities`；
- [ ] `tests/run_tests.py` 里加一个 `test_<名字>_*`：`detect()` 的两条路径（有指纹 / 无指纹）、
      `state_dir()`、以及 `read_transcript()` 在**伪造载荷**下的归一化输出；
- [ ] 如果实现了 `read_transcript`，拿一份**真实的**会话记录跑一遍
      `python -I scripts/scan.py <真实的.jsonl> --top 12`，确认统计数字对得上；
- [ ] 如果实现了 `block_tool`，真的构造一次超限调用，确认退出码是 2 且 stderr 是 UTF-8；
- [ ] 拿不准的地方**在文件头写清"未实测"**，并留下对照能力表的待办清单。

最后一条不是客套。`adapter_codex.py` 就是这么写的——它的 `detect()` / `state_dir()` 是确定的，
其余全部标注"未在真机验证过"，因为它没在装了 codex 的机器上跑过。**把没验证过的东西写成
已经能用，比不写还糟。**
