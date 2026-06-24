# 简道云智能助手配置采集工具 (RPA) v8.1

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **本项目是一个 RPA 工具**，通过浏览器自动化技术采集简道云「智能助手」配置信息。
>
> 简道云官方 Open API 不提供智能助手（自动化工作流）的配置查询接口，因此本项目采用 RPA 方式，模拟人工操作浏览器来获取这些数据。

## 功能特性

- **GUI 界面**：提供 tkinter 图形界面，右键即可运行，无需命令行
- **多浏览器支持**：自动检测 Edge / Chrome / Chromium，无需安装专用浏览器
- **跨平台**：支持 Windows / macOS / Linux
- **RPA 自动化**：通过 CDP 协议控制浏览器，自动导航、点击、提取配置
- **完整采集**：采集画布节点流程、字段映射关系、查询条件、分支规则等
- **数据清洗**：智能去除 UI 噪音，提取有效业务数据
- **业务分类**：自动识别助手类型（状态更新型、数据同步型、级联删除型等）
- **结构化输出**：生成 JSON 原始数据 + Markdown 报告

## 快速开始

### 1. 安装依赖

```bash
pip install playwright
```

> 不需要运行 `playwright install chromium`，本工具直接使用系统已安装的 Edge 或 Chrome。

### 2. 配置

复制配置模板并填入你的应用 ID：

```bash
cp scripts/config.example.json scripts/config.json
```

编辑 `scripts/config.json`：

```json
{
    "app_id": "你的简道云应用ID",
    "output_dir": "",
    "cdp_url": "http://localhost:9222",
    "enable_screenshots": false,
    "include_empty_field_mappings": false,
    "skip_form_keywords": ["未启用", "废弃", "草稿", "停用"]
}
```

> `config.json` 已在 `.gitignore` 中，不会被提交到仓库。

### 3. 运行

**GUI 方式（推荐）：**

```bash
python scripts/gui_capture.py
```

点击「启动浏览器」→ 登录简道云 → 点击「连接浏览器 & 获取模块」→ 勾选模块 → 点击「开始采集」

**CLI 方式：**

```bash
# 先启动浏览器（自动检测 Edge/Chrome）
python scripts/launch_browser.py

# 在浏览器中登录简道云，然后运行采集
python scripts/capture_all_assistants.py                  # 交互式选择模块
python scripts/capture_all_assistants.py 销售管理          # 指定模块
python scripts/capture_all_assistants.py 销售管理,采购管理  # 多模块
python scripts/capture_all_assistants.py --list-modules    # 只列出模块
```

## 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `app_id` | 简道云应用 ID | - |
| `output_dir` | 输出目录（空则为 `./output`） | `""` |
| `cdp_url` | 浏览器 CDP 调试地址 | `http://localhost:9222` |
| `enable_screenshots` | 是否截图（关闭可提速） | `false` |
| `include_empty_field_mappings` | 是否输出空值字段映射 | `false` |
| `skip_form_keywords` | 跳过包含这些关键字的表单 | `["未启用", "废弃", "草稿", "停用"]` |

**优先级**：CLI 参数 > `config.json` > 默认值

## 输出结构

```
{输出目录}/
└── {模块名}_{时间戳}/
    ├── 过程文件/              # JSON 原始数据
    │   ├── a00_{时间}_result.json       # 单个助手数据
    │   ├── {表单名}_summary.json        # 表单汇总
    │   └── {模块名}_summary.json        # 模块汇总
    ├── 截图/                  # 截图文件（如开启）
    └── 报告/                  # Markdown 报告
        ├── {表单名}_report.md
        ├── {模块名}_summary.md
        └── GLOBAL_SUMMARY_{时间}.md
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `gui_capture.py` | GUI 界面（推荐使用） |
| `capture_all_assistants.py` | 核心采集脚本 |
| `launch_browser.py` | 浏览器启动工具（自动检测 Edge/Chrome） |
| `test_field_mapping.py` | 字段映射解析单元测试 |
| `config.json` | 配置文件（不提交 Git） |
| `config.example.json` | 配置模板（提交 Git） |

## 节点类型识别

| DOM Class | 识别类型 |
|-----------|----------|
| trigger-data-node | 触发 |
| query-data-single-node-icon | 查询单条 |
| query-data-multi-node-icon | 查询多条 |
| update-data-node-icon | 修改 |
| create-data-node-icon | 新增 |
| delete-data-node-icon | 删除 |
| branch-node-icon | 分支 |
| calculate-node-icon | 计算 |
| ai-node-icon | AI |
| message-node-icon | 消息 |

## 业务类型自动分类

| 类型 | 特征 | 节点类型 |
|------|------|----------|
| 状态更新型 | 流程结束后同步更新状态 | 触发 |
| 数据同步型 | 跨表单级联更新 | 查询+修改/新增 |
| 级联删除型 | 主数据删除时清理关联数据 | 删除 |
| 单号补充型 | 补充关联子表单号 | 修改 |
| MRP运算型 | 物料需求计划计算 | 计算+查询+新增 |
| 消息通知型 | 发送企业微信/邮件 | 消息 |

## 故障排查

| 问题 | 解决方案 |
|------|----------|
| 未找到浏览器 | 确保已安装 Edge 或 Chrome |
| 连接超时 | 检查浏览器是否以 CDP 模式启动（端口 9222） |
| 提取 0 个模块 | 确认已在浏览器中登录并进入目标应用 |
| 表单无助手 | 该表单可能没有以它为触发源的助手 |
| 节点配置缺失 | 脚本会自动跳过失败节点，继续处理下一个 |

## License

[MIT](LICENSE)

---

**免责声明**：本项目仅供学习交流使用，请遵守简道云服务条款。
