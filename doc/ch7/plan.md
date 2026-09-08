# YuCode 品牌迁移与首页锦鲤 Plan

## 架构概览

本次是一次不保留兼容层的全量重命名。发布元数据决定安装包、命令和构建产物名称；Python 包目录决定模块导入与 `python -m` 入口；配置与权限路径决定运行期读取及本地规则保存位置；终端组件负责用户可见品牌和首页锦鲤。测试和文档随这些契约一并迁移。

不引入名称映射、别名模块或旧配置回退。所有内部导入直接指向 `yucode`，确保旧名称一旦残留即可被搜索和测试发现。

## 核心数据结构

本次不新增业务数据结构。现有配置、权限、Agent 和工具对象保持字段与行为不变；仅更新它们所在的包路径、默认文件名和对用户展示的品牌文本。

运行期名称约定如下：

| 用途 | 新名称 |
|---|---|
| 产品展示名 | `YuCode` |
| Python 包与导入前缀 | `yucode` |
| 控制台命令 | `yucode` |
| 主配置 | `yucode.yaml` |
| 项目权限配置 | `yucode.permissions.yaml` |
| 本地权限配置 | `yucode.permissions.local.yaml` |
| 用户权限目录 | `~/.yucode/permissions.yaml` |
| 提示缓存版本/键前缀 | `yucode` |

## 模块设计

### 发布与入口

**职责：** 声明 YuCode 的可安装分发名、控制台入口、wheel 包路径和 `python -m yucode` 启动方式。

**对外接口：** 用户通过 `yucode` 或 `python -m yucode` 启动。`旧名称` 不再是入口，也不提供旧包导入。

**依赖：** 所有应用模块从 `yucode` 导入。

### 配置与权限路径

**职责：** 在未指定路径时，只查找 `yucode.yaml`；权限规则只从新的用户、项目和本地文件名读取或写入。

**对外接口：** `load_config(path=None)` 的显式路径参数语义不变；省略参数时默认路径改为 `yucode.yaml`。权限管理器的显式路径覆盖能力不变，默认路径统一为新名称。

**依赖：** `yucode.permissions`、`yucode.config` 和 CLI 共同使用这些默认路径。

### 终端欢迎页

**职责：** 在首条对话前展示 YuCode、小锦鲤字符画、当前目录和模型信息。

**对外接口：** 欢迎组件仍接收提供商与模型；仅替换静态字符画和产品文字。

**依赖：** 不依赖新增资源或外部图像；沿用现有 Textual 富文本居中渲染与配色。

### 测试与文档

**职责：** 以新的包名、命令和配置名验证行为，并将项目说明、章节文档和配置示例统一为 YuCode。

**对外接口：** 自动化测试继续覆盖配置加载、CLI 构建、TUI 欢迎页、权限存储和完整 Agent 流程；README 只给出新命令及新配置名。

**依赖：** 依赖最终的发布、包路径和运行期默认路径。

## 模块交互

```text
用户执行 yucode / python -m yucode
            │
            ▼
yucode.cli → yucode.config（读取 yucode.yaml）
            │
            ├→ yucode.permissions（读取/写入 yucode 权限路径）
            └→ yucode.tui（展示 YuCode + 锦鲤欢迎页）
                         │
                         ▼
                    原有 Agent/工具对话流程
```

## 文件组织

```text
project/
├── pyproject.toml                         # 分发名、命令和 wheel 包路径
├── yucode.yaml.example                    # 主配置示例
├── yucode.permissions.yaml.example        # 项目权限规则示例
├── src/yucode/                            # 原 src/旧名称 的完整迁移目录
│   ├── __main__.py                        # python -m yucode
│   ├── config.py                          # yucode.yaml 默认路径
│   ├── permissions.py                     # 新的三层权限默认路径
│   └── tui/widgets.py                     # YuCode 欢迎文字与小锦鲤字符画
├── tests/                                 # 所有导入、测试文件名与断言迁移
├── README.md                              # YuCode 使用说明
└── doc/                                   # 现有章节资料与本章开发文档
```

`src/旧名称/`、`旧名称.yaml.example` 和 `旧名称.permissions.yaml.example` 会在迁移后删除；不保留同名转发文件。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 迁移策略 | 全量直接改名，不设兼容层 | 符合“彻底切换、旧的冗余都可删去”，避免双入口和长期维护负担。 |
| Python 重命名方式 | 物理移动 `src/旧名称` 为 `src/yucode`，逐一改导入 | 可保证旧包不再可导入，构建产物与源码结构一致。 |
| 配置兼容性 | 只读取 `yucode.yaml` 和新的权限文件名 | 防止旧配置被悄然使用，让行为符合彻底切换的预期。 |
| 锦鲤呈现 | 内嵌多行 Unicode/ASCII 字符画，不新增图片资源 | 终端稳定、无需资源加载，并保持现有 TUI 的文本设计。 |
| 锁定依赖 | 重建锁定元数据以反映新分发名 | 避免安装环境继续记录旧项目名称。 |
| 历史文档 | 更新受版本控制的项目文档中的产品名称与命令示例 | 让仓库内可见项目资料保持单一品牌；不重写 Git 历史。 |
