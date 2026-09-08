# YuCode 品牌迁移与首页锦鲤 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `pyproject.toml`、`uv.lock` | 迁移分发名、命令、wheel 包路径和锁定元数据。 |
| 重命名 | `src/旧名称/` → `src/yucode/` | 移除旧 Python 包并建立唯一的新包路径。 |
| 修改 | `src/yucode/**/*.py` | 更新内部导入、品牌文案、配置/权限默认文件名和提示缓存前缀。 |
| 修改 | `src/yucode/tui/widgets.py` | 将欢迎页小猫和 旧名称 改为小锦鲤与 YuCode。 |
| 重命名/修改 | `旧名称*.yaml.example` → `yucode*.yaml.example`、`.gitignore` | 迁移示例和忽略项。 |
| 修改 | `tests/**/*.py` | 更新导入、测试配置名、断言和 monkeypatch 路径。 |
| 修改 | `README.md`、`doc/**/*.md` | 统一可见项目说明、章节文档和命令示例。 |
| 新建 | `doc/ch7/checklist.md` | 将验收标准变为可执行检查项。 |

## T1: 迁移发布元数据与源码包

**文件：** `pyproject.toml`、`uv.lock`、`src/旧名称/`、`src/yucode/`

**依赖：** 无

**步骤：**
1. 将分发名、控制台命令和 wheel 打包路径改为 `yucode`。
2. 将源码目录物理重命名为 `src/yucode`，不保留 `src/旧名称`。
3. 更新包元数据与模块启动入口，使 `python -m yucode` 进入现有 CLI。
4. 重建锁定元数据，使本地安装不再声明 旧名称 分发名。

**验证：** 运行 `uv run python -c "import yucode; print(yucode.__version__)"` 和 `uv run python -m yucode --help`（或在缺少配置时观察新配置名提示）；期望新包可导入、模块入口可执行，且 `import 旧名称` 失败。

## T2: 迁移内部依赖、默认路径与产品身份

**文件：** `src/yucode/**/*.py`

**依赖：** T1

**步骤：**
1. 将所有内部绝对导入改为 `yucode` 前缀。
2. 将模块文档字符串、应用身份提示、缓存版本与缓存键中的旧品牌改为新品牌。
3. 将默认主配置、项目规则、本地规则和用户规则目录改为新的 yucode 名称，不增加旧路径回退。
4. 保持现有函数签名、配置字段和权限/工具行为不变。

**验证：** 运行 `uv run pytest tests/test_config.py tests/test_permissions.py tests/test_prompting.py -q`；期望配置、权限和提示行为通过，并从报错文字中看到 `yucode.yaml`。

## T3: 更换欢迎页为小锦鲤

**文件：** `src/yucode/tui/widgets.py`、`tests/test_tui.py`

**依赖：** T1、T2

**步骤：**
1. 用多行小锦鲤字符画替换欢迎组件内的小猫字符画。
2. 将欢迎标题改为 YuCode，保留现有配色、居中、目录和模型信息。
3. 更新布局测试，断言 YuCode 与锦鲤的可识别字符，并断言不再显示旧名称或猫咪字符画。

**验证：** 运行 `uv run pytest tests/test_tui.py -q`；期望 TUI 组件测试通过，欢迎页断言确认品牌和锦鲤已替换。

## T4: 迁移配置示例、忽略规则与自动化测试

**文件：** `yucode.yaml.example`、`yucode.permissions.yaml.example`、`.gitignore`、`tests/**/*.py`

**依赖：** T1、T2、T3

**步骤：**
1. 将两份配置示例物理重命名为 yucode 名称，并更新相关说明。
2. 将新主配置和本地权限规则加入忽略列表，移除旧名称忽略项。
3. 将所有测试导入、临时配置文件名、断言、补丁目标和命令文本切换到 yucode。
4. 增加或更新测试，证明默认启动只查找新配置名，并且旧包无法导入。

**验证：** 运行 `uv run pytest -q`；期望完整测试集通过，且测试仅引用 yucode 路径和配置名。

## T5: 统一项目说明与章节文档

**文件：** `README.md`、`doc/**/*.md`

**依赖：** T1、T4

**步骤：**
1. 将 README 的产品名、配置文件名和启动命令切换为 YuCode/yucode。
2. 将受版本控制的章节文档中的当前产品名、包路径、配置名和终端命令同步替换为新名称。
3. 保留章节语义和完成记录，不修改 Git 历史，也不触及无关的用户未跟踪文本文件。

**验证：** 运行 `git grep -n -i "旧名称\|旧名称" -- ':!uv.lock'`；期望无匹配。运行 `git diff --check`；期望无空白错误。

## T6: 按清单完成集成与终端验收

**文件：** `doc/ch7/checklist.md`、受 T1–T5 影响的文件

**依赖：** T1、T2、T3、T4、T5

**步骤：**
1. 根据批准后的验收清单依次运行品牌搜索、安装入口、配置、自动化测试和欢迎页检查。
2. 使用 tmux 启动 `python -m yucode`，确认小锦鲤欢迎页和 YuCode 标题正常显示。
3. 在同一 tmux 会话中输入一段真实对话请求；观察模型请求、工具调用（如适用）和最终回复，以及输入框恢复可用。
4. 在清单中记录每项实际命令、观察结果与通过状态；失败项先修复再重跑。

**验证：** 完整执行 `doc/ch7/checklist.md`；期望所有适用项目通过，并有 tmux 端到端观察记录。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6
```
