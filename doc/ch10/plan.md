# 上下文状态展示与窗口保护 Plan

## 架构概览

本次不改变上下文压缩策略，而是让上下文管理层在“实际开始压缩”和“处理完成”两个时点分别产出状态事件。Agent 将这些事件原样转发给 TUI；TUI 根据结构化字段渲染普通字重的中文单行文本。这样压缩开始能在摘要请求前显示，完成后又能显示同一次历史的前后 Token。

自动检查仍会在小窗口测试配置下运行，但当没有早期历史时，上下文管理层不产生可展示的自动跳过事件，且不会创建摘要请求。手动 `/compact` 继续产生跳过事件，保留用户主动操作的反馈。

## 核心数据结构

### 扩展后的 `ContextResult`

```python
@dataclass(frozen=True)
class ContextResult:
    action: ContextAction
    status: Literal["unchanged", "offloaded", "compacting", "compacted", "failed", "circuit_open"]
    detail: str = ""
    offloaded_count: int = 0
    released_characters: int = 0
    before_tokens: int | None = None
    after_tokens: int | None = None
```

`offloaded` 使用 `released_characters` 表示本次重写历史减少的字符数；`compacting` 携带压缩前估算 Token；`compacted` 同时携带前后值。`unchanged` 仅用于手动命令反馈或内部自动控制流，不再是自动 UI 日志。

### 上下文操作事件流

```python
async def prepare_request(...) -> AsyncIterator[ContextResult]: ...
async def compact_manually(...) -> AsyncIterator[ContextResult]: ...
async def compact_emergency(...) -> AsyncIterator[ContextResult]: ...
```

把原有“等待压缩结束后一次性返回结果”改为异步事件流。轻量外置完成后立即 yield；真正需要摘要且存在早期历史时先 yield `compacting`，再等待模型摘要并 yield 最终结果。不存在压缩源时，自动路径不 yield，手动路径只 yield `unchanged`。

## 模块设计

### `src/yucode/context.py`

**职责：** 计算外置释放字符数与压缩前后估算 Token，并以事件流报告上下文处理进度。

**对外接口：** 扩展 `ContextResult`；将三种公开操作和内部压缩方法改为异步迭代器。

**依赖：** 既有 TokenBudgetTracker、Conversation、Provider 与缓存存储。

外置前后均对同一会话历史计算字符数，差值填入 `released_characters`。重量压缩在确认存在 `source` 后才发出 `compacting`，因此小窗口首轮只有检查、没有摘要请求和开始状态。正式摘要提交到 Conversation 后立即用同一预算跟踪器估算 `after_tokens`。自动路径没有 source 时直接结束；手动路径保持“没有可压缩的较早历史”的结果。

### `src/yucode/agent.py`

**职责：** 消费上下文操作事件流，并及时转成既有 `ContextUpdated` 事件。

**对外接口：** `Agent.run` 与 `Agent.compact` 的事件类型不变；内部把一次 `await` 改为 `async for`。

**依赖：** ContextManager 新的异步事件流接口。

Agent 在普通请求前按收到顺序转发外置、压缩开始和压缩完成事件；紧急压缩仅在收到成功完成事件后重试。手动命令同样转发全部事件，不改变取消和错误收尾行为。

### `src/yucode/tui/app.py` 与 `src/yucode/tui/widgets.py`

**职责：** 将结构化上下文事件渲染为图片所示的紧凑状态行。

**对外接口：** `ContextActivity` 继续只接收最终文本和失败标志；App 新增纯函数或私有渲染分支将 `ContextResult` 转为文本。

渲染规则如下：

| 状态 | 显示文本 |
|---|---|
| `offloaded` | `已外置 N 个工具结果到磁盘 · C 字符已释放` |
| `compacting` | `正在压缩上下文…` |
| `compacted` | `已压缩上下文 · Before → After 估算 Token` |
| 手动 `unchanged` | `没有可压缩的较早历史` |
| `failed` / `circuit_open` | 保留现有清晰中文原因 |

`ContextActivity` 的标记和正文均移除 `bold` 样式；工具结果和普通消息组件不改动。App 过滤自动 `unchanged`，保证 13001 测试配置的首轮普通对话不产生状态行。

### `tests/test_context.py`、`tests/test_agent.py` 与 `tests/test_tui.py`

**职责：** 验证事件顺序、精确统计、静默规则和样式。

**对外接口：** 无。

测试应覆盖外置字符释放量、压缩开始早于摘要 Provider 请求、压缩 Token 前后值、13001 小窗口首轮不发摘要也不产生 UI 状态、手动无历史提示、非加粗 ContextActivity，以及原有 `/compact` 与紧急重试回归。

### `src/yucode/config.py`、`yucode.yaml` 与 `yucode.yaml.example`

**职责：** 保持小窗口测试配置合法，并说明 128K 是默认值。

**对外接口：** 配置下限与字段不变；不修改用户的 13001 测试值。示例继续展示默认 128K。

## 模块交互

```text
Agent 请求前
  │
  └─ ContextManager.prepare_request()
       ├─ 外置成功 ───────────────→ offloaded(C 字符) ─→ TUI 状态行
       ├─ 自动检查无早期历史 ────→ 不产生事件、无摘要请求、TUI 静默
       └─ 实际重量压缩
            ├─ compacting(Before Token) ─────────────→ TUI“正在压缩”
            └─ compacted(Before, After Token) ──────→ TUI 压缩结果

/compact ─→ compact_manually()
  └─ 无早期历史 ────────────────→ unchanged ───────→ TUI 手动跳过提示
```

## 文件组织

```text
src/yucode/
├── context.py       # 事件流、释放字符数与 Token 前后值
├── agent.py         # 转发上下文事件流
└── tui/
    ├── app.py       # 结构化状态渲染与自动静默过滤
    └── widgets.py   # 非加粗 ContextActivity
tests/
├── test_context.py  # 统计、事件顺序、小窗口静默
├── test_agent.py    # 紧急重试与事件流回归
└── test_tui.py      # 文本、样式、手动与自动状态
doc/ch10/
├── spec.md
└── plan.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 进度传递 | ContextManager 使用异步事件流 | 只有它能在等待摘要 API 时把“开始压缩”提前交给界面。 |
| 外置统计 | 比较外置前后历史字符数 | 反映实际从模型对话中移除的内容，且无需 tokenizer。 |
| 压缩统计 | 使用同一个 TokenBudgetTracker 的前后估算 | 数据口径一致，符合当前近似估算策略。 |
| 自动无历史 | 不产生上下文事件 | 13001 等测试配置仍验证触发条件，但不污染普通对话界面。 |
| 手动无历史 | 保留 `unchanged` 事件 | 用户主动输入命令时需要明确反馈。 |
| 样式 | 只移除 ContextActivity 的粗体 | 精确满足视觉请求，不影响工具、错误与普通消息的既有层次。 |
