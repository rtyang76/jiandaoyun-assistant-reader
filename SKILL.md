---
name: jiandaoyun-assistant-reader
description: >
  查询简道云应用中各表单的智能助手（自动化工作流）配置信息。
  支持按模块遍历、自动导航、截图采集、结构化数据导出。
  触发词：智能助手查看、自动化采集、workflow抓取、助手配置导出
---

# 简道云智能助手批量查看 Skill

## 概述

自动化浏览简道云应用中每个表单的「智能助手」（即自动化工作流）配置，提取：
- 每个助手的画布节点流程图
- 各节点的详细配置参数
- 字段映射关系
- 结构化 JSON + Markdown 报告

## 使用方式

### CLI 命令

```bash
cd ~/.workbuddy/skills/jiandaoyun-assistant-reader/scripts

# 交互式选择模块（手动输入）
python3 capture_all_assistants.py

# 指定模块名
python3 capture_all_assistants.py 销售管理

# 指定多个模块
python3 capture_all_assistants.py 销售管理,生产管理,采购管理

# 只列出所有模块和表单（不执行采集）
python3 capture_all_assistants.py --list-modules

# 自定义应用ID和输出目录
python3 capture_all_assistants.py 销售管理 --app-id=YOUR_APP_ID --output=/path/to/output
```

### Agent 调用方式

在 WorkBuddy agent 中，直接描述需求即可：

> "帮我跑一下销售管理模块的智能助手采集"

Agent 会将自然语言转换为 `python3 capture_all_assistants.py 销售管理` 执行。

## 执行流程 (v7.0)

```
┌─────────────────────────────────────────────┐
│  启动: 连接 Chromium (CDP localhost:9222)    │
└──────────────────────┬──────────────────────┘
                       ▼
┌─────────────────────────────────────────────┐
│  Phase A: 提取应用导航树                      │
│  ├─ 导航到应用主页                            │
│  ├─ 展开所有文件夹（最多3轮）                   │
│  └─ 提取模块-表单树 + 颜色分类                 │
└──────────────────────┬──────────────────────┘
                       ▼
┌─────────────────────────────────────────────┐
│  模块选择                                    │
│  ├─ CLI参数 / Agent传入 → 自动匹配             │
│  └─ 交互模式 → 显示编号列表供选择              │
└──────────────────────┬──────────────────────┘
                       ▼
┌─────────────────────────────────────────────┐
│  Phase B: 按模块遍历表单                       │
│  对每个模块中的每个有效表单：                    │
│  ├─ 导航到表单编辑页 → 智能助手tab              │
│  ├─ 筛选"作为触发动作"                         │
│  └─ 逐个助手采集：                             │
│     ├─ 点击编辑按钮（4策略）                    │
│     ├─ 截取画布全貌（滚动截图）                  │
│     ├─ 逐节点点击 → 配置面板截图+数据提取        │
│     ├─ 配置面板滚动截取完整内容                  │
│     └─ 返回列表，处理下一个                     │
└──────────────────────┬──────────────────────┘
                       ▼
┌─────────────────────────────────────────────┐
│  Phase C: 生成报告                           │
│  ├─ 每个表单: JSON + MD 报告                 │
│  ├─ 每个模块: 汇总MD + 统计JSON              │
│  └─ 全局: GLOBAL_SUMMARY (跨模块总览)         │
└─────────────────────────────────────────────┘
```

## 颜色过滤规则

简道云左侧导航图标颜色与类型的对应：

| 颜色 | 类型 | 处理 |
|------|------|------|
| 🟠 橙色 | 流程表单 | ✅ 采集 |
| 🔵 蓝色 | 普通表单 | ✅ 采集 |
| 🟣 紫色 | 仪表盘/报表 | ⏭️ 跳过 |
| 🟡 黄色 | 文件夹(容器) | ⏭️ 跳过 |

## 输出结构

```
{OUTPUT_DIR}/                          # 默认 /tmp/jdy_assistants_v7/
├── navigation_tree_raw.json           # 原始导航树（调试用）
├── GLOBAL_SUMMARY_YYYYMMDD_HHMMSS.md  # 全局汇总报告
├── GLOBAL_SUMMARY_YYYYMMDD_HHMMSS.json # 全局汇总JSON
│
├── 销售管理/                           # 模块目录
│   ├── 销售管理_summary.md            # 模块汇总报告
│   ├── 销售管理_summary.json          # 模块统计JSON
│   │
│   ├── 销售订单/                       # 表单目录
│   │   ├── form_summary.json          # 表单级汇总
│   │   ├── 销售订单_report.md          # 表单详细报告
│   │   ├── a00_xxxx_page.png          # 助手0 列表页截图
│   │   ├── a00_xxxx_cv00.png          # 画布截图1
│   │   ├── a00_xxxx_n00_config.png    # 节点0配置面板
│   │   ├── a00_xxxx_n00_drawer.png    # 节点0配置滚动截图
│   │   ├── a00_xxxx_result.json       # 助手0 完整结构化数据
│   │   ├── a01_xxxx_...               # 助手1 ...
│   │   └── zero_result.json           # 无助手时的标记文件
│   │
│   └── 销售出库单/                     # 下一个表单...
│
├── 生产管理/                           # 下一个模块...
...
```

## 前置条件

1. **Chromium 浏览器已启动并开启调试端口**:
   ```bash
   /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
   ```
2. **浏览器已登录简道云**，且停留在任意页面即可（脚本会自动导航）

## 技术细节

- **浏览器连接**: Playwright CDP 连接 `localhost:9222`
- **编辑按钮点击**: 4层策略 — Playwright click > JS el.click() > 全页匹配行号 > mouse.click坐标
- **筛选状态**: 自动检测当前是否已处于"作为触发动作"，避免重复操作
- **导航树提取**: JS evaluate 解析左侧 sidebar DOM，支持二级嵌套文件夹

## 注意事项

- 当前为**只读操作**，不会修改任何智能助手配置
- 退出画布时会自动点击"不保存"丢弃可能的未保存变更
- 输出路径默认使用 `/tmp/`，重启后会清理。如需保留请用 `--output=` 参数指定持久化路径
