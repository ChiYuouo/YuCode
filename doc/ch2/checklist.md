# YuCode 工具系统 Checklist

> 每项均以可观察行为、测试输出或 tmux 中的实际交互验收。

## 工具能力

- [x] AC1：OpenAI 与 Claude 后端均能使用六项工具。验证：`pytest -q` 通过；两类 Provider 均覆盖工具 Schema、碎片调用和结果序列化，注册中心覆盖六项工具。
- [x] AC2：文件类工具只能访问工作目录。验证：工具测试覆盖目录内读取与 `..` 越界拒绝。
- [x] AC3：写文件可创建和覆盖，改文件只在唯一匹配时成功。验证：工具测试通过。
- [x] AC4：改文件零次或多次匹配不会改变原文件。验证：工具测试通过，重复匹配返回 `multiple_matches`。
- [x] AC5：命令必须确认。验证：TUI 测试与 tmux 中均出现 `Get-Location` 确认弹窗；拒绝后未执行并回灌结果。
- [x] AC6：失败可恢复。验证：测试覆盖参数、越界、无效正则、非零退出、超时和意外 Provider 异常；55 项测试均通过。
- [x] AC7：碎片化流式参数能被正确重组。验证：OpenAI 与 Claude Provider 测试通过。
- [x] AC8：每轮最多执行一个工具。验证：会话测试确认只执行首项并拒绝额外及第二次调用。
- [x] AC9：界面只展示工具摘要。验证：TUI 测试和 tmux 实测均显示工具、目标与摘要，未显示完整文件内容。

## 协议与集成

- [x] OpenAI 请求正确携带函数工具声明、富历史和 `function_call_output`。验证：Provider 测试通过。
- [x] Claude 请求正确携带工具声明、`tool_use` 和 `tool_result` 内容块。验证：Provider 测试通过。
- [x] 既有纯文本、thinking、用量、网络错误与取消行为没有回归。验证：`pytest -q` 通过。
- [x] 命令确认期间的拒绝、关闭弹窗或 `Ctrl+C` 不会卡住后台线程。验证：TUI 测试通过。

## 构建与自动化测试

- [x] 项目安装入口可加载。验证：tmux 中以 `.venv\\Scripts\\python.exe -m yucode` 启动，成功进入 TUI。
- [x] 全部自动化测试通过。验证：`.venv\\Scripts\\python -m pytest -q`，55 项通过，退出码 0。

## 端到端场景

- [x] 文件读取闭环：tmux 中输入英文等价请求后，模型调用 `read_file` 读取 `README.md`，显示成功摘要并回复项目用途，输入恢复可用。
- [ ] 搜索闭环：同配置的非 TUI 真实请求成功调用 `search_code` 并回复 `src/yucode/providers/base.py:110`；但 WSL tmux 与 Windows Python 组合下该场景出现不稳定挂起，尚不能作为 tmux 通过证据。
- [x] 命令拒绝闭环：tmux 中请求 `Get-Location`，出现确认弹窗；选择拒绝后显示拒绝摘要，模型说明未执行，输入恢复可用。
