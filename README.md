# 简道云智能助手批量采集工具 v8.1

自动化采集简道云应用中各表单的「智能助手」（自动化工作流）配置信息，生成结构化报告与原始数据。

## 功能特性

- **自动化导航**：从简道云左侧导航自动提取模块-表单树，支持二级文件夹
- **智能采集**：自动点击「编辑」按钮，提取每个助手的节点流程与详细配置
- **数据清洗**：智能去除UI噪音（按钮文本、提示信息等），提取有效业务数据
- **业务分类**：自动识别助手类型（状态更新型、数据同步型、级联删除型等）
- **结构化输出**：生成 JSON 原始数据 + Markdown 报告
- **智能截图**：按需截图（只保留关键节点配置），减少80%冗余图片

## 环境准备

### 1. 启动 Chromium（CDP 模式）

```bash
# 使用 launch_browser.py 脚本启动
python3 scripts/launch_browser.py

# 或手动启动
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-debug-profile
```

### 2. 登录简道云

在启动的浏览器中登录简道云并进入目标应用。

## 使用方法

### CLI 命令

```bash
cd scripts

# 交互式选择模块
python3 capture_all_assistants.py

# 指定单个模块
python3 capture_all_assistants.py 销售管理

# 指定多个模块（逗号分隔）
python3 capture_all_assistants.py 销售管理,生产管理,采购管理

# 只列出所有模块和表单
python3 capture_all_assistants.py --list-modules
```

### 配置项

编辑 `capture_all_assistants.py` 修改以下全局配置：

```python
# 输出根目录
OUTPUT_DIR = "/Users/yrt/Developer/Work/erp-data-analysis/智能助手采集数据"

# 默认应用ID
APP_ID = "69db868cc68a628a7d0f207f"

# 截图开关（默认关闭以提升速度）
ENABLE_SCREENSHOTS = False

# 字段映射空值输出开关（默认不输出未配置的空值字段）
INCLUDE_EMPTY_FIELD_MAPPINGS = False

# 表单名称排除关键字
SKIP_FORM_KEYWORDS = ['未启用', '废弃', '测试', '草稿', '停用']
```

## 输出结构

```
{输出目录}/
└── {模块名}_{时间戳}/
    ├── 过程文件/              # JSON原始数据
    │   ├── a00_{时间}_result.json       # 单个助手数据
    │   ├── {表单名}_summary.json        # 表单汇总
    │   ├── {模块名}_summary.json        # 模块汇总
    │   └── GLOBAL_SUMMARY_{时间}.json   # 全局汇总
    ├── 截图/                  # 截图文件（如开启）
    │   └── ...
    └── 报告/                  # Markdown报告
        ├── {表单名}_report.md             # 表单详情报告
        ├── {模块名}_summary.md            # 模块汇总报告
        └── GLOBAL_SUMMARY_{时间}.md       # 全局汇总报告
```

## 报告内容示例

### Markdown 报告结构

```markdown
# 表单名称

表单ID: xxx | 模块: xxx | 助手数: n

## 1. 助手名称
**流程**:
1. [触发] 节点名称
   - 目标表单: xxx
   - 查询条件: ...
   - 字段映射:
     - 字段A = 节点1 → 字段X
     - 字段B = `自定义值`
```

### JSON 数据结构

```json
{
  "index": 0,
  "name": "助手名称",
  "triggerEvent": "修改数据",
  "businessType": "数据同步型",
  "businessDescription": "跨表单数据级联更新",
  "nodes": [
    {
      "index": 0,
      "name": "触发节点",
      "type": "触发",
      "config": {
        "header": "表单触发",
        "fields": [...],
        "fieldMappings": [
          {
            "field": "目标字段",
            "sourceType": "node|custom|empty",
            "sourceNode": "源节点",
            "sourceField": "源字段",
            "customValue": "自定义值"
          }
        ]
      }
    }
  ]
}
```

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
| webhook-node-icon | Webhook |

## 业务类型自动分类

根据节点组合自动标记助手类型：

| 类型 | 特征 | 节点类型 |
|------|------|----------|
| 状态更新型 | 流程结束后同步更新状态 | 触发 |
| 数据同步型 | 跨表单级联更新 | 查询+修改/新增 |
| 级联删除型 | 主数据删除时清理关联数据 | 删除 |
| 单号补充型 | 补充关联子表单号 | 修改 |
| MRP运算型 | 物料需求计划计算 | 计算+查询+新增 |
| 消息通知型 | 发送企业微信/邮件 | 消息 |

## 注意事项

1. **浏览器要求**：必须使用 CDP 模式启动 Chromium，确保 `--remote-debugging-port=9222`
2. **登录状态**：运行前需已在浏览器中登录简道云
3. **页面跳转**：采集过程中不要手动操作浏览器，避免干扰自动化流程
4. **超时处理**：若某助手采集失败，脚本会自动跳过并继续处理下一个
5. **资源占用**：截图功能会生成大量图片文件，建议在 SSD 上运行

## 故障排查

### 无法连接浏览器

```
❌ 无法找到右侧「所有本表相关」下拉框
```

**解决**：确保 Chromium 已正确启动并开启了远程调试端口

### 列表为空

```
⚠️ 该表单无智能助手（作为触发动作）
```

**说明**：该表单可能没有配置以它为触发源的助手，或筛选未正确执行

### 节点采集超时

```
⚠️ 配置抽屉加载超时
```

**处理**：脚本会使用备用延时继续执行，部分节点的详细配置可能缺失

## 版本历史

### v8.1.0
- 新增 `INCLUDE_EMPTY_FIELD_MAPPINGS` 配置，控制是否输出空值字段
- 重构输出目录结构：过程文件/截图/报告 三分离
- 修复字段映射空值过滤逻辑

### v8.0
- 智能清洗节点名称与配置文本
- 自动业务类型分类
- 智能截图策略（减少80%冗余）
- 规范化目录结构

### v7.x
- 支持二级文件夹展开
- 颜色过滤（跳过仪表盘/文件夹）
- CLI交互式模块选择
- 结构化字段映射解析

## 文件说明

| 文件 | 说明 |
|------|------|
| `capture_all_assistants.py` | 主采集脚本 |
| `launch_browser.py` | 浏览器启动工具 |
| `test_field_mapping.py` | 字段映射解析单元测试 |

## License

内部工具，仅供项目团队使用。
