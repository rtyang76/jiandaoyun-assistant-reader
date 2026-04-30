#!/usr/bin/env python3
"""
简道云智能助手批量截取脚本 v8.0 - 数据质量优化版

v8.0 重大改进：
1. 【文本质量】智能清洗节点名称，去除"编辑/查看/复制/删除"等UI噪音
2. 【节点识别】从header字段推导真实节点类型，修正"分支/计算/循环"识别
3. 【业务分类】自动根据节点组合标记助手类型（状态更新型/数据同步型/级联删除型等）
4. 【截图策略】只保留关键截图：画布全貌+复杂节点配置，减少80%冗余图片
5. 【目录结构】规范化输出：模块_时间/文档/截图 二级目录
6. 【数据纯净】去除无效字段，优化JSON结构，减少AI处理噪音

v7.0-v7.3 历史功能：
- 从左侧导航提取模块-表单树（支持二级文件夹）
- 颜色过滤：橙色(流程表单)+蓝色(普通表单)保留，紫色(仪表盘)+黄色(文件夹)跳过
- CLI交互式选择要遍历的模块
- 结构化字段映射解析

使用方式：
  python3 capture_all_assistants.py                    # CLI 交互模式，手动输入模块名
  python3 capture_all_assistants.py 销售管理            # 直接指定模块名
  python3 capture_all_assistants.py --list-modules      # 只列出所有模块和表单
"""
import asyncio
import json
import os
import sys
from datetime import datetime

# ============================================================
# 全局配置（运行时由 Phase A 动态设置）
# ============================================================
APP_ID = "672f1dc45d82b890f5231d52"
OUTPUT_DIR = "/Users/yrt/Developer/Work/erp-data-analysis/智能助手采集数据"
FORM_ID = ""          # 当前处理的表单ID（动态变化）
FORM_NAME = ""        # 当前表单名称（动态变化）
MODULE_NAME = ""      # 当前模块名称（动态变化）

# 节点类型常量（与 NODE_TYPE_MAP 的值保持一致，集中管理避免字符串散落）
class NodeType:
    TRIGGER      = '触发'
    QUERY_SINGLE = '查询单条'
    QUERY_MULTI  = '查询多条'
    UPDATE       = '修改'
    CREATE       = '新增'
    DELETE       = '删除'
    BRANCH       = '分支'
    COMPARE      = '条件比较'
    CALCULATE    = '计算'
    AI           = 'AI'
    AI_EXTRACT   = 'AI提取'
    MESSAGE      = '消息'
    WEBHOOK      = 'Webhook'
    LOOP_START   = '循环开始'
    LOOP_END     = '循环结束'
    PROCESS      = '流程节点'
    DELAY        = '延迟'
    CONDITION    = '条件'
    FALLBACK     = '其他条件'  # 兜底分支（不可配置）
    UNKNOWN      = '未知'

NODE_TYPE_MAP = {
    'trigger-data-node':          NodeType.TRIGGER,
    'query-data-single-node-icon': NodeType.QUERY_SINGLE,
    'query-data-multi-node-icon': NodeType.QUERY_MULTI,
    'update-data-node-icon':      NodeType.UPDATE,
    'create-data-node-icon':      NodeType.CREATE,
    'delete-data-node-icon':      NodeType.DELETE,
    'branch-node-icon':           NodeType.BRANCH,
    'branch-compare-node-icon':   NodeType.COMPARE,
    'calculate-node-icon':        NodeType.CALCULATE,
    'ai-node-icon':               NodeType.AI,
    'ai-extract-node-icon':       NodeType.AI_EXTRACT,
    'message-node-icon':          NodeType.MESSAGE,
    'webhook-node-icon':          NodeType.WEBHOOK,
    'loop-start-node-icon':       NodeType.LOOP_START,
    'loop-end-node-icon':         NodeType.LOOP_END,
    'process-node-icon':          NodeType.PROCESS,
    'delay-node-icon':            NodeType.DELAY,
    'condition-node-icon':        NodeType.CONDITION,
}

# 简道云左侧导航 SVG 图标 class 对应的类型
# （基于 .fx-app-menu-tree.fx-indicator-tree 中的 svg.x-biz-entry-icon class）
# group: 文件夹/模块容器（黄色）→ 跳过
# flow:  流程表单（橙色）→ 采集
# form:  普通表单（蓝色）→ 采集
# dash:  仪表盘/看板（紫色）→ 跳过
ICON_TYPE_TO_ENTRY_TYPE = {
    'flow': ('form_process', '流程表单'),
    'form': ('form_normal', '普通表单'),
}

# 跳过的类型：文件夹(group)和仪表盘(dash)不作为表单处理（但会自动展开其子表单）
SKIP_ICON_TYPES = {'group', 'dash'}

# v8.0: 模块目录映射表
MODULE_DIRS = {}

# v8.0.5: 截图开关（默认关闭以提升速度）
ENABLE_SCREENSHOTS = False

# v8.0.9: 表单名称排除关键字（包含这些关键字的表单将被跳过）
SKIP_FORM_KEYWORDS = ['未启用', '废弃', '草稿', '停用']

# v8.1.0: 字段映射空值输出开关（默认不输出未配置的空值字段）
INCLUDE_EMPTY_FIELD_MAPPINGS = False

# 兜底分支节点的可能名称（点击后不打开抽屉，需跳过采集）
_FALLBACK_BRANCH_NAMES = {NodeType.FALLBACK, '所有条件都不满足时，执行本分支', '所有条件都不满足时执行本分支'}


def should_skip_form(form_name):
    """检查表单是否应该被跳过（根据名称关键字）"""
    return any(kw in form_name for kw in SKIP_FORM_KEYWORDS)


def _skip_keyword(form_name):
    """返回匹配的跳过关键字，无匹配返回 None"""
    for kw in SKIP_FORM_KEYWORDS:
        if kw in form_name:
            return kw
    return None


# ============================================================
# v8.0 新增：文本清洗与业务分类工具函数
# ============================================================

_BASE_NOISE_WORDS = [
    '编辑', '查看', '复制', '删除', '触发',
    '添加动作', '添加条件', '添加字段', '快捷填充',
]

UI_NOISE_WORDS = _BASE_NOISE_WORDS + [
    '执行日志',
    '当', '时', '的数据', '满足所有条件',
    '修改后',
    '满足所有条件的数据', '选择表单', '修改数据',
    '新增数据', '删除数据', '查询出', '查询出满足所有条件的数据',
]

CONFIG_NOISE_PATTERNS = _BASE_NOISE_WORDS + [
    '添加排序规则',
    '当 的数据 满足所有条件 时', '当 的数据 满足任意条件 时',
    '请选择', '点击选择', '未设置', '未选择',
]

# 业务类型分类规则
BUSINESS_TYPE_RULES = [
    {
        'type': '级联删除型',
        'description': '主数据删除时自动清理关联数据',
        'keywords': ['删除'],
        'node_types': [NodeType.DELETE],
    },
    {
        'type': '状态更新型',
        'description': '流程结束或状态变更后同步更新状态字段',
        'keywords': ['状态', '流程结束', '审批'],
        'node_types': [NodeType.TRIGGER],
    },
    {
        'type': '数据同步型',
        'description': '跨表单数据级联更新，通常包含查询+修改/新增',
        'keywords': ['同步', '更新', '联动'],
        'node_types': [NodeType.QUERY_SINGLE, NodeType.QUERY_MULTI, NodeType.UPDATE, NodeType.CREATE],
    },
    {
        'type': '单号补充型',
        'description': '为主数据补充关联子表单号或编号',
        'keywords': ['单号', '编号', '编码'],
        'node_types': [NodeType.UPDATE],
    },
    {
        'type': 'MRP运算型',
        'description': '物料需求计划计算，包含BOM展开和库存运算',
        'keywords': ['BOM', '物料需求', 'MRP', '计划'],
        'node_types': [NodeType.CALCULATE, NodeType.QUERY_MULTI, NodeType.UPDATE, NodeType.CREATE],
    },
    {
        'type': '消息通知型',
        'description': '触发后发送企业微信/邮件/短信通知',
        'keywords': ['通知', '提醒', '预警', '告警'],
        'node_types': [NodeType.MESSAGE],
    },
]


def clean_assistant_name(raw_name: str) -> tuple:
    """
    清洗助手名称，去除触发事件后缀，同时提取触发事件。
    
    返回: (cleaned_name, trigger_event)
    
    示例:
        "写入-其他入库表触发：库存调拨 — 修改数据" 
        -> ("写入-其他入库表", "修改数据")
    """
    if not raw_name:
        return '', ''
    
    name = raw_name.strip()
    trigger_event = ''
    
    # 提取触发事件（最后一个 "—" 或 "-" 后面的部分）
    # 匹配 "— 修改数据" 或 " - 新增数据" 等格式
    for sep in [' — ', ' - ', '触发：']:
        if sep in name:
            parts = name.rsplit(sep, 1)  # 只分割最后一次
            if len(parts) == 2:
                name = parts[0].strip()
                trigger_event = parts[1].strip()
                break
    
    # 进一步清洗名称中的噪音
    # 去除常见的无意义后缀
    _SUFFIX_NOISE = [NodeType.TRIGGER, '编辑', '查看', '复制', NodeType.DELETE]
    for suffix in _SUFFIX_NOISE:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
    
    return name, trigger_event


def clean_node_name(raw_name: str) -> str:
    """清洗节点名称，去除UI噪音"""
    if not raw_name:
        return ''
    
    name = raw_name.strip()
    
    # 去除已知的噪音词
    for noise in UI_NOISE_WORDS:
        name = name.replace(noise, '')
    
    # 去除连续空格
    name = ' '.join(name.split())
    
    # 去除常见的无意义前缀
    _PREFIX_NOISE = [
        f'{NodeType.UPDATE}数据 - ', f'{NodeType.CREATE}数据 - ',
        f'{NodeType.DELETE}数据 - ', '查询数据 - ',
    ]
    for prefix in _PREFIX_NOISE:
        if name.startswith(prefix):
            name = name[len(prefix):]
    
    return name.strip()


def get_node_type_from_header(header: str, cls: str = '') -> str:
    """从header字段和class综合判断节点类型"""
    header_mapping = {
        '分支条件':   NodeType.BRANCH,
        '计算节点':   NodeType.CALCULATE,
        '查询单条数据': NodeType.QUERY_SINGLE,
        '查询多条数据': NodeType.QUERY_MULTI,
        '修改数据':   NodeType.UPDATE,
        '新增数据':   NodeType.CREATE,
        '删除数据':   NodeType.DELETE,
        '表单触发':   NodeType.TRIGGER,
        '循环容器':   NodeType.LOOP_START,
        '发送消息':   NodeType.MESSAGE,
        'AI节点':    NodeType.AI,
        'Webhook':   NodeType.WEBHOOK,
    }

    for key, value in header_mapping.items():
        if key in header:
            return value

    for k, v in NODE_TYPE_MAP.items():
        if k in cls:
            return v

    return NodeType.UNKNOWN


def classify_assistant_business_type(assistant_data: dict) -> dict:
    """根据助手数据自动分类业务类型"""
    nodes = assistant_data.get('nodes', [])
    name = assistant_data.get('name', '')
    
    node_types = [n.get('type', '') for n in nodes]
    node_names = ' '.join([n.get('name', '') for n in nodes])
    
    scores = {}
    for rule in BUSINESS_TYPE_RULES:
        score = 0
        # 关键词匹配
        for kw in rule['keywords']:
            if kw in name or kw in node_names:
                score += 2
        # 节点类型匹配
        for nt in rule['node_types']:
            if nt in node_types:
                score += 3
        
        if score > 0:
            scores[rule['type']] = {
                'score': score,
                'description': rule['description'],
            }
    
    # 返回得分最高的类型
    if scores:
        best_type = max(scores.items(), key=lambda x: x[1]['score'])
        return {
            'businessType': best_type[0],
            'businessDescription': best_type[1]['description'],
            'confidence': min(best_type[1]['score'] * 10, 100),  # 转换为百分比
        }
    
    return {
        'businessType': '通用型',
        'businessDescription': '常规数据处理流程',
        'confidence': 0,
    }


def should_capture_node_screenshot(node_type: str, node_config: dict) -> bool:
    """判断是否需要截图该节点的配置"""
    complex_types = {NodeType.BRANCH, NodeType.LOOP_START, NodeType.CALCULATE, NodeType.AI, NodeType.TRIGGER}
    if node_type in complex_types:
        return True

    if node_config:
        if node_config.get('branchRule') or node_config.get('branchRuleActive'):
            return True
        if len(node_config.get('fields', [])) > 5:
            return True

    return False


def clean_config_noise(config: dict) -> dict:
    """
    清理 config 中的 UI 噪音（v8.0）。
    
    清理内容:
    - fields.body 中的 "添加动作" 等 UI 文本
    - mappings 中的噪音文本
    
    v8.0.9: 不清理 "满足所有条件"、"满足任一条件"（分支节点需要）
    """
    if not config:
        return config

    # 清理 fields
    if 'fields' in config and isinstance(config['fields'], list):
        cleaned_fields = []
        for field in config['fields']:
            if isinstance(field, dict):
                # 清理 body 字段
                body = field.get('body', '')
                if isinstance(body, str):
                    for noise in CONFIG_NOISE_PATTERNS:
                        body = body.replace(noise, '').strip()
                    # 去除连续空格
                    body = ' '.join(body.split())
                    field['body'] = body

                # 清理 title 字段
                title = field.get('title', '')
                if isinstance(title, str):
                    for noise in CONFIG_NOISE_PATTERNS:
                        title = title.replace(noise, '').strip()
                    field['title'] = title
                
                # 只保留非空的字段
                if field.get('title') or field.get('body'):
                    cleaned_fields.append(field)
        config['fields'] = cleaned_fields
    
    # 清理 mappings（只保留有效的映射）
    if 'mappings' in config and isinstance(config['mappings'], list):
        cleaned_mappings = []
        for mapping in config['mappings']:
            if isinstance(mapping, str):
                # 跳过纯噪音的映射
                is_noise_only = any(noise in mapping for noise in CONFIG_NOISE_PATTERNS)
                if not is_noise_only and len(mapping.strip()) > 3:
                    cleaned_mappings.append(mapping)
            elif mapping:
                cleaned_mappings.append(mapping)
        config['mappings'] = cleaned_mappings
    
    return config


def get_node_type(cls):
    """从 CSS class 判断节点类型"""
    for k, v in NODE_TYPE_MAP.items():
        if k in cls:
            return v
    return NodeType.UNKNOWN


async def connect():
    from playwright.async_api import async_playwright
    playwright = await async_playwright().start()
    browser = await playwright.chromium.connect_over_cdp("http://localhost:9222")
    # headless-shell 没有预打开页面，需要手动创建
    if browser.contexts and browser.contexts[0].pages:
        page = browser.contexts[0].pages[0]
    else:
        ctx = browser.contexts[0] if browser.contexts else browser
        page = await ctx.new_page()
    return playwright, browser, page


async def get_url_state(page):
    url = page.url
    if '/form/' not in url:
        return 'unknown', url
    if '/edit#/' in url:
        return 'form_edit', url
    if '/edit#' in url:
        return 'form_edit_partial', url
    if '#/app/' in url:
        return 'form_home', url
    return 'other', url


async def debug_dump(page, label="debug", out_dir=None):
    """截图 + DOM 关键结构输出（受 ENABLE_SCREENSHOTS 控制）"""
    if not ENABLE_SCREENSHOTS:
        return
    d = out_dir or OUTPUT_DIR
    ts = datetime.now().strftime('%H%M%S')
    await page.screenshot(path=f"{d}/{label}_{ts}.png")
    dom_info = await page.evaluate("""
        () => {
            const items = document.querySelectorAll('.fx-automation-extension-item');
            const result = [];
            items.forEach((item, i) => {
                const buttons = item.querySelectorAll('button');
                const btnInfo = [];
                buttons.forEach(b => {
                    const r = b.getBoundingClientRect();
                    btnInfo.push({
                        text: b.textContent?.trim(),
                        cls: b.className.substring(0, 80),
                        rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
                        visible: r.width > 0 && r.height > 0
                    });
                });
                const rowRect = item.getBoundingClientRect();
                result.push({
                    index: i,
                    text: item.textContent?.trim().substring(0, 60),
                    rect: {x: Math.round(rowRect.x), y: Math.round(rowRect.y), w: Math.round(rowRect.width), h: Math.round(rowRect.height)},
                    buttons: btnInfo
                });
            });
            return result;
        }
    """)
    print(f"    [DEBUG] {label}: {json.dumps(dom_info, ensure_ascii=False, indent=2)[:500]}")
    return dom_info


# ============================================================
# Phase A: 提取应用导航树
# ============================================================
# 基于简道云真实DOM结构（v7.1 重写）：
#
#   .fx-app-menu-tree.fx-indicator-tree
#     └─ :scope > .tree-node          ← 所有节点都是直接子元素
#          ├─ span.node-indent[style="width:0px"]  ← 层级缩进（每级20px）
#          ├─ div.node-content-wrapper
#          │    ├─ div.node-icon > svg.x-biz-entry-icon.{group|flow|form|dash}
#          │    └─ div.node-content
#          │         ├─ div.entry-name > span.name-text  ← 名称
#          │         └─ (可选) span.fx-entry-node[href="/app/.../form/{id}"]
#
# 图标 class 对应类型：
#   group = 📁 文件夹/模块（黄色）→ 需展开，本身不采集
#   flow  = 🟠 流程表单（橙色）→ 采集
#   form  = 🔵 普通表单（蓝色）→ 采集
#   dash  = 🟣 仪表盘（紫色）→ 跳过
# ============================================================


async def _scroll_tree(page, scroll_to='bottom', reset=True):
    """滚动导航树以触发懒加载。

    Args:
        scroll_to: 'bottom' 滚到底部, 'top' 滚到顶部
        reset: 是否在滚动后回滚到顶部
    """
    js = """
        (opts) => {
            const tree = document.querySelector('.fx-app-menu-tree.fx-indicator-tree');
            if (!tree) return;
            tree.scrollTop = opts.scrollTo === 'bottom'
                ? tree.scrollHeight * 10
                : 0;
            if (opts.reset) {
                setTimeout(() => { tree.scrollTop = 0; }, 100);
            }
        }
    """
    await page.evaluate(js, {'scrollTo': scroll_to, 'reset': reset})


async def navigate_to_app_home(page, app_id):
    """导航到应用主页（显示左侧模块列表）"""
    target_url = f"https://www.jiandaoyun.com/dashboard#/app/{app_id}"
    
    # 检查是否已经在正确的页面
    if '/app/' + app_id in page.url and '#/app/' in page.url:
        print("  ✓ 已在应用主页")
        await page.wait_for_timeout(1500)
        return

    print(f"  → 导航到应用主页: {target_url}")
    await page.goto(target_url, wait_until='networkidle')
    
    # 等待左侧导航树加载完成
    try:
        await page.wait_for_selector('.fx-app-menu-tree.fx-indicator-tree', timeout=10000)
        print("  ✓ 导航树已加载")
    except Exception as e:
        print(f"  ⚠️ 等待导航树超时，继续: {e}")
        await page.wait_for_timeout(5000)

    # 如果在某个表单内，返回到应用首页
    state, _ = await get_url_state(page)
    if state == 'form_edit':
        back_btn = await page.query_selector('.fx-navigation-bar-back-btn')
        if back_btn:
            await back_btn.click()
            await page.wait_for_timeout(2000)
        else:
            await page.goto(target_url, wait_until='networkidle')
            await page.wait_for_timeout(4000)


async def extract_all_tree_nodes(page):
    """
    提取 fx-indicator-tree 中所有可见的 .tree-node 节点。
    
    返回原始扁平列表，每个节点包含：
      index, name, level(通过indent width), iconType(group/flow/form/dash),
      formId(从href提取), href, treeNodeKey
    
    不做层级归并——留给 build_module_structure() 处理。
    """
    print("  提取导航树节点...")

    await _scroll_tree(page, reset=True)
    await page.wait_for_timeout(800)

    result = await page.evaluate("""
        () => {
            const tree = document.querySelector('.fx-app-menu-tree.fx-indicator-tree');
            if (!tree) return {error: '未找到 .fx-app-menu-tree.fx-indicator-tree'};

            // 递归获取所有 tree-node（包括嵌套的二级目录下的表单）
            function collectNodes(element, parentLevel = -1) {
                const items = [];
                const directNodes = element.querySelectorAll(':scope > .tree-node');
                
                for (let i = 0; i < directNodes.length; i++) {
                    const node = directNodes[i];

                    // --- 名称 ---
                    const nameTextEl = node.querySelector('.name-text');
                    const name = nameTextEl ? nameTextEl.textContent?.trim() : '';

                    // --- 层级：通过 .node-indent 的 width 计算 ---
                    let indentWidth = 0;
                    let level = 0;
                    const indentEl = node.querySelector('.node-indent');
                    if (indentEl) {
                        const style = indentEl.getAttribute('style') || '';
                        const m = style.match(/width:\\s*(\\d+)px/i);
                        if (m) {
                            indentWidth = parseInt(m[1]);
                            level = Math.round(indentWidth / 20);
                        }
                    }

                    // --- 图标类型：svg.x-biz-entry-icon ---
                    const svgIcon = node.querySelector('svg.x-biz-entry-icon');
                    let iconType = 'unknown';
                    if (svgIcon) {
                        const cls = svgIcon.getAttribute('class') || '';
                        if (cls.includes('group')) iconType = 'group';
                        else if (cls.includes('flow')) iconType = 'flow';
                        else if (cls.includes('form')) iconType = 'form';
                        else if (cls.includes('dash')) iconType = 'dash';
                    }

                    // --- formId：从 span.fx-entry-node[href] 正则提取 ---
                    const entryNode = node.querySelector(
                        'span.fx-entry-node[href], a.fx-entry-node[href]'
                    );
                    let formId = '';
                    let href = '';
                    if (entryNode) {
                        href = entryNode.getAttribute('href') || '';
                        const fm = href.match(/form\\/([a-f0-9]{24})/);
                        if (fm) formId = fm[1];
                    }

                    items.push({
                        name: name,
                        level: level,
                        indentWidth: indentWidth,
                        iconType: iconType,
                        formId: formId,
                        href: href ? href.substring(0, 100) : ''
                    });
                    
                    // 递归获取子节点（tree-children 内的节点）
                    const childContainer = node.querySelector('.tree-children');
                    if (childContainer) {
                        const childItems = collectNodes(childContainer, level);
                        items.push(...childItems);
                    }
                }

                return items;
            }

            const items = collectNodes(tree);
            
            return {totalNodes: items.length, items: items};
        }
    """)

    return result


async def expand_folder_modules(page):
    """
    递归展开所有 group 文件夹节点（包括二级目录）。
    
    策略：
    1. 每轮扫描所有可见的 tree-node
    2. 找到所有未展开的 group 节点（通过 expanded 属性或子节点存在性判断）
    3. 点击展开
    4. 重复直到没有新的展开
    
    返回: 是否成功保持在主页状态
    """
    print("  展开文件夹节点...")
    total_expanded = 0
    stayed_on_home = True

    for round_num in range(10):  # 增加轮数，确保二级目录也能展开
        # 先检查是否还在主页
        still_ok = await page.evaluate("""
            () => !!document.querySelector('.fx-app-menu-tree.fx-indicator-tree')
        """)
        if not still_ok:
            print(f"    ⚠️ 第{round_num+1}轮前: 导航树消失，停止展开")
            stayed_on_home = False
            break

        expanded_in_round = await page.evaluate("""
            () => {
                const tree = document.querySelector('.fx-app-menu-tree.fx-indicator-tree');
                if (!tree) return -1;

                // 递归获取所有 tree-node，包括嵌套的
                function getAllTreeNodes(element) {
                    const nodes = [];
                    const directNodes = element.querySelectorAll(':scope > .tree-node');
                    directNodes.forEach(node => {
                        nodes.push(node);
                        // 递归获取子节点
                        const childNodes = getAllTreeNodes(node);
                        nodes.push(...childNodes);
                    });
                    return nodes;
                }

                const allNodes = getAllTreeNodes(tree);
                let count = 0;

                for (let i = 0; i < allNodes.length; i++) {
                    const n = allNodes[i];

                    // 检查是否是 group 类型
                    const svgIcon = n.querySelector('svg.x-biz-entry-icon');
                    let isGroup = false;
                    if (svgIcon) {
                        const cls = svgIcon.getAttribute('class') || '';
                        isGroup = cls.includes('group');
                    }
                    if (!isGroup) continue;

                    // 检查是否已经展开（通过子节点存在性判断）
                    const hasExpanded = n.querySelector('.tree-children') !== null;
                    if (hasExpanded) continue;

                    // 点击展开
                    const contentWrapper = n.querySelector('.node-content-wrapper');
                    if (contentWrapper) {
                        contentWrapper.click();
                        count++;
                    }
                }

                return count;
            }
        """)

        if expanded_in_round == -1:
            print(f"    ⚠️ 导航树在展开过程中消失")
            stayed_on_home = False
            break

        if expanded_in_round > 0:
            total_expanded += expanded_in_round
            print(f"    第{round_num+1}轮: 点击了 {expanded_in_round} 个文件夹")
            await page.wait_for_timeout(1200)
            
            # 检查树是否还在
            still_ok = await page.evaluate("""
                () => !!document.querySelector('.fx-app-menu-tree.fx-indicator-tree')
            """)
            if not still_ok:
                print(f"    ⚠️ 展开后导航树消失，可能触发了页面跳转")
                stayed_on_home = False
                break
            
            await _scroll_tree(page, scroll_to='bottom', reset=False)
            await page.wait_for_timeout(500)
        else:
            print(f"    第{round_num+1}轮: 没有更多可展开的文件夹")
            break

    print(f"  ✓ 共点击了 {total_expanded} 个文件夹节点 {'(⚠️ 页面可能已离开主页)' if not stayed_on_home else ''}")
    return total_expanded, stayed_on_home


def build_module_structure(raw_items):
    """
    将扁平的 raw_items 列表转为模块-子项层级结构。
    
    分组规则：
    - L0 + iconType=group → 新模块开始
    - 后续 L>0 且非 group 的节点 → 归入当前模块
    - 遇到下一个 L0 节点 → 开始新模块
    
    返回格式与 v7.0 一致：
    [
      {"name": "销售管理", "type": "folder", "children": [
         {"name": "报价单", "type": "flow", "formId": "..."},
         ...
      ]},
      ...
    ]
    """
    modules = []
    current_module = None

    for item in raw_items:
        name = item.get('name', '')
        level = item.get('level', 0)
        icon_type = item.get('iconType', 'unknown')

        if level == 0 and icon_type == 'group':
            # 新的顶层模块文件夹
            current_module = {
                "name": name,
                "type": "folder",
                "children": []
            }
            modules.append(current_module)
        elif current_module is not None and level >= 1:
            # 归入当前模块的子项
            child_entry = {
                "name": name,
                "type": icon_type,
                "formId": item.get('formId', ''),
                "depth": level
            }
            current_module['children'].append(child_entry)

    return modules


async def build_module_tree(page, app_id):
    """
    完整构建模块树（v8.0 优化版）。
    
    分两阶段：
    Phase A-1: 提取模块列表（L0 group 节点）→ 用于用户选择
    Phase A-2: 展开选中模块 → 提取子表单
    """
    global APP_ID
    APP_ID = app_id

    print("\n" + "=" * 60)
    print("Phase A: 提取应用模块树 (v8.0)")
    print("=" * 60)
    print(f"  应用ID: {app_id}")

    # Step 1: 导航到主页
    await navigate_to_app_home(page, app_id)

    # Step 2: 滚动确保全部可见节点加载
    await _scroll_tree(page, reset=False)
    await page.wait_for_timeout(800)
    
    await _scroll_tree(page, scroll_to='top', reset=False)
    await page.wait_for_timeout(500)

    # Step 3: 提取当前可见的全部节点
    raw_result = await extract_all_tree_nodes(page)

    if isinstance(raw_result, dict) and raw_result.get('error'):
        print(f"  ❌ 导航树提取失败: {raw_result.get('error')}")
        
        # 备选：尝试展开后再提取
        print("  → 尝试展开后重新提取...")
        expand_result, stayed = await expand_folder_modules(page)
        if not stayed:
            await navigate_to_app_home(page, app_id)
            await page.wait_for_timeout(2000)
        
        raw_result = await extract_all_tree_nodes(page)

    items = raw_result.get('items', [])
    print(f"\n  提取到 {len(items)} 个原始节点")

    # Step 4: 构建初始模块结构（可能有些模块还没有子项）
    module_tree = build_module_structure(items)

    # 打印摘要
    total_forms = count_valid_forms(module_tree)
    print(f"\n  ✓ 导航树提取完成:")
    print(f"    模块数: {len(module_tree)}")
    print(f"    有效表单数: {total_forms}")

    for mod in module_tree:
        # v8.0.9: 过滤有效表单并检查关键字
        valid_children = []
        keyword_skipped_count = 0
        for c in mod.get('children', []):
            if c.get('type') in SKIP_ICON_TYPES:
                continue
            if should_skip_form(c.get('name', '')):
                keyword_skipped_count += 1
                continue
            valid_children.append(c)
        skipped = [c for c in mod.get('children', []) if c.get('type') in SKIP_ICON_TYPES]
        skip_info = f" (跳过{len(skipped)}个类型+{keyword_skipped_count}个关键字)" if (skipped or keyword_skipped_count) else ""
        print(f"    📁 {mod['name']}: {len(valid_children)}个有效表单{skip_info}")
        for c in mod.get('children', []):
            ctype = c.get('type', '?')
            cname = c.get('name', '')
            if ctype in SKIP_ICON_TYPES:
                mark = "⏭️"
            elif should_skip_form(cname):
                mark = "⏭️"
            else:
                mark = "✅"
            fid_short = c.get('formId', '')[:8] + '..' if c.get('formId') else ''
            print(f"       {mark} [{ctype:5s}] {cname}  id={fid_short}")

    return module_tree


def count_valid_forms(tree):
    """统计有效表单数量（排除类型和关键字）"""
    count = 0
    for mod in tree:
        for child in mod.get('children', []):
            if child.get('type') in SKIP_ICON_TYPES:
                continue
            # v8.0.9: 检查关键字排除
            form_name = child.get('name', '')
            if should_skip_form(form_name):
                continue
            count += 1
    return count


def select_target_modules(tree, user_input=None):
    """
    选择要遍历的目标模块。
    支持CLI参数、agent传入、或交互式选择。
    """
    if not tree:
        print("❌ 无可用模块")
        return []

    module_names = [m['name'] for m in tree]

    # 方式1: 用户通过参数指定
    if user_input:
        # 支持逗号分隔的多模块
        targets = [s.strip() for s in user_input.split(',')]
        selected = []

        for t in targets:
            matched = None
            for m in tree:
                if m['name'] == t or t in m['name']:
                    matched = m
                    break
            if matched:
                selected.append(matched)
            else:
                # 模糊匹配
                fuzzy_matches = [m for m in tree if t in m['name'] or m['name'] in t]
                if fuzzy_matches:
                    selected.extend(fuzzy_matches)
                else:
                    print(f"  ⚠️ 未找到匹配的模块: {t}")

        if selected:
            names = [m['name'] for m in selected]
            print(f"\n  ✓ 已选择模块: {', '.join(names)}")
            return selected

    # 方式2: 交互式选择
    print(f"\n{'='*60}")
    print("  请选择要遍历的模块（输入编号，多个用逗号分隔）:")
    print(f"{'='*60}")

    for i, m in enumerate(tree):
        valid_count = sum(1 for c in m.get('children', [])
                          if c.get('type') not in SKIP_ICON_TYPES)
        print(f"  {i+1}. {m['name']} ({valid_count}个表单)")

    print(f"  0. 全部模块")
    print()

    try:
        choice = input("  输入选择: ").strip()
    except EOFError:
        # 非 TTY 环境（如 agent 调用），默认全部
        print("  (非交互模式，默认选择全部)")
        choice = "0"

    if choice == "0" or choice.strip() == "":
        return tree

    try:
        indices = [int(x.strip()) - 1 for x in choice.split(',') if x.strip().isdigit()]
        selected = [tree[i] for i in indices if 0 <= i < len(tree)]
        if selected:
            names = [m['name'] for m in selected]
            print(f"  ✓ 已选择: {', '.join(names)}")
            return selected
    except ValueError:
        pass

    # 如果解析失败，尝试模糊名称匹配
    for m in tree:
        if choice in m['name']:
            print(f"  ✓ 匹配到: {m['name']}")
            return [m]

    print(f"  ⚠️ 无效选择 '{choice}'，默认执行第一个模块")
    return [tree[0]]


# ============================================================
# Phase B: 单表单采集（复用v6.0逻辑）
# ============================================================

async def check_filter_state(page):
    """检查「所有本表相关」下拉框当前筛选状态
    
    智能助手列表页面有两个下拉框（v7.3 修正）：
    - 左侧：「所有触发方式」→ 触发模式筛选（❌ 不是这个）
    - 右侧：「所有本表相关」→ 本表关系筛选（✅ 是这个）
    
    右侧下拉选项包含：
    - 所有本表相关（默认，显示全部）
    - 作为触发动作（只显示本表触发的智能助手）
    
    返回值说明：
    - 'trigger_only': 已选中「作为触发动作」
    - 'show_all':     显示「所有本表相关」（未筛选状态）
    - 'list_loaded_N': 列表已加载 N 个条目
    - 'unknown':       无法确定状态
    """
    result = await page.evaluate("""
        () => {
            // === 策略1：精确匹配右侧下拉框 ===
            // 页面上有多个下拉框，必须找到显示「所有本表相关」或「作为触发动作」的那个
            // 通过 title 属性或文本来定位右侧下拉
            
            // 方法A：查找包含目标文本的下拉框 value-content
            const allValueContents = document.querySelectorAll('.value-content');
            for (const vc of allValueContents) {
                const txt = vc.textContent?.trim();
                if (txt === '作为触发动作') return 'trigger_only';
                if (txt === '所有本表相关') return 'show_all';
            }
            
            // 方法B：通过 title 属性查找（右侧下拉框的容器通常带此 title）
            const rightDropdown = document.querySelector('[title="所有本表相关"], [title="作为触发动作"]');
            if (rightDropdown) {
                const txt = rightDropdown.getAttribute('title');
                if (txt === '作为触发动作') return 'trigger_only';
                if (txt === '所有本表相关') return 'show_all';
                
                // 也检查其内部文本
                const innerTxt = rightDropdown.textContent?.trim();
                if (innerTxt === '作为触发动作') return 'trigger_only';
                if (innerTxt === '所有本表相关') return 'show_all';
            }
            
            // 方法C：从下拉选项中判断哪个被 selected
            const dropdownItems = document.querySelectorAll('.x-combo-dropdown-item.selected');
            for (const item of dropdownItems) {
                const title = item.getAttribute('title') || '';
                const txt = item.textContent?.trim() || '';
                if (title === '作为触发动作' || txt === '作为触发动作') return 'trigger_only';
                if (title === '所有本表相关' || txt === '所有本表相关') return 'show_all';
            }
            
            // === 回退：通过列表数量判断 ===
            const rows = document.querySelectorAll('.fx-automation-extension-item');
            if (rows.length > 0) return 'list_loaded_' + rows.length;

            return 'unknown';
        }
    """)
    return result


async def find_dropdown_trigger(page):
    """查找右侧「所有本表相关」下拉框的位置
    
    v7.3 修正：智能助手列表页面有两组下拉：
    - 左侧：「所有触发方式」（❌ 错误目标）
    - 右侧：「所有本表相关」（✅ 正确目标，红框位置）
    
    必须精确匹配右侧下拉框。
    """
    result = await page.evaluate("""
        () => {
            // === 策略1（最可靠）：通过 title 属性定位右侧下拉 ===
            // 右侧下拉框容器通常带有 title="所有本表相关"
            let el = document.querySelector('[title="所有本表相关"]');
            if (el && el.offsetParent !== null) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    return {x: r.left + r.width/2, y: r.top + r.height/2, found: true, method: 'title-本表相关'};
                }
            }
            
            // 也尝试作为触发动作的title（如果已经被切换过了）
            el = document.querySelector('[title="作为触发动作"]');
            if (el && el.offsetParent !== null) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    return {x: r.left + r.width/2, y: r.top + r.height/2, found: true, method: 'title-触发动作'};
                }
            }
            
            // === 策略2：通过文内容匹配 value-content ===
            // 页面上可能有多个 .value-content，找包含目标文本的那个
            const allValueContents = document.querySelectorAll('.value-content');
            for (const vc of allValueContents) {
                const txt = vc.textContent?.trim();
                if (txt === '所有本表相关' || txt === '作为触发动作') {
                    const r = vc.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        return {x: r.left + r.width/2, y: r.top + r.height/2, found: true, method: 'value-content-' + txt};
                    }
                }
            }
            
            // === 策略3：查找页面右侧区域内的 x-combo-box ===
            // 智能助手标题栏右侧的下拉框（排除左侧导航区域的）
            const allCombos = document.querySelectorAll('.x-combo-box');
            let bestCandidate = null;
            let bestX = -1;
            for (const combo of allCombos) {
                const r = combo.getBoundingClientRect();
                if (r.width > 0 && r.height > 0 && r.x > 400) {  // 右侧区域（x>400）
                    if (r.x > bestX) {
                        bestX = r.x;
                        bestCandidate = combo;
                    }
                }
            }
            if (bestCandidate) {
                const r = bestCandidate.getBoundingClientRect();
                return {x: r.left + r.width/2, y: r.top + r.height/2, found: true, method: 'rightmost-combo'};
            }
            
            return {found: false};
        }
    """)
    if result.get('found'):
        print(f"    找到右侧下拉框: ({result['x']:.0f}, {result['y']:.0f}) [{result.get('method')}]")
    else:
        print("    ⚠️ 未找到「所有本表相关」下拉框")
    return result


async def find_trigger_option(page):
    """查找「作为触发动作」选项的位置"""
    result = await page.evaluate("""
        () => {
            const items = document.querySelectorAll('.x-combo-dropdown-item');
            for (const item of items) {
                const title = item.getAttribute('title');
                const txt = item.textContent?.trim();
                if ((title === '作为触发动作' || txt === '作为触发动作') && item.offsetParent !== null) {
                    const r = item.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        return {x: r.left + r.width/2, y: r.top + r.height/2, found: true};
                    }
                }
            }
            return {found: false};
        }
    """)
    if result.get('found'):
        print(f"    找到选项: ({result['x']:.0f}, {result['y']:.0f})")
    return result


async def _get_assistant_rows(page):
    """获取当前页面的智能助手行数"""
    rows = await page.query_selector_all('.fx-automation-extension-item')
    return len(rows)


async def filter_trigger_only(page, output_dir=None, force_refilter=False):
    """筛选本表触发的智能助手（通过右侧「所有本表相关」下拉框）
    
    v7.3 修正：入口是页面右上角的「所有本表相关」下拉框，
    而不是左侧的「所有触发方式」。
    
    Args:
        output_dir: 截图输出目录
        force_refilter: 是否强制重新执行筛选（即使当前看起来已处于筛选状态）
                        表单切换后必须设为 True，因为上一个表单的状态可能残留
    """
    d = output_dir or OUTPUT_DIR
    ts = datetime.now().strftime('%H%M%S')

    # 多次检测状态（页面可能还在加载）
    filter_state = 'unknown'
    for detect_retry in range(3):
        filter_state = await check_filter_state(page)
        if filter_state != 'unknown':
            break
        print(f"    状态检测返回 {filter_state}，等待页面加载... ({detect_retry+1}/3)")
        await page.wait_for_timeout(1500)

    print(f"  右侧下拉框状态: {filter_state} (强制重筛={force_refilter})")

    # 已处于筛选完成状态（非强制模式时跳过）
    if not force_refilter:
        if filter_state == 'trigger_only' or (filter_state.startswith('list_loaded_') and filter_state != 'list_loaded_0'):
            count_match = filter_state.startswith('list_loaded_')
            count = int(filter_state.split('_')[-1]) if count_match else 0
            if filter_state == 'trigger_only' or count > 0:
                print(f"  ✓ 已处于「作为触发动作」筛选状态（{filter_state}），跳过筛选步骤")
                await page.wait_for_timeout(1500)
                return await _get_assistant_rows(page)

    print("  → 需要点击右侧「所有本表相关」下拉框，选择「作为触发动作」")

    print("  [Step 1] 查找右侧「所有本表相关」下拉框...")
    trigger_pos = await find_dropdown_trigger(page)

    if not trigger_pos or not trigger_pos.get('found'):
        # [v7.3 容错] 找不到下拉框时，不直接致命
        # 可能原因：
        # 1) 页面还没完全渲染 → 等待后重试
        # 2) 上一个表单的残留状态下拉框文本被识别为「作为触发动作」但DOM结构不同
        # 3) 该表单确实没有智能助手列表区域
        
        # 先检查是否已有列表数据（可能已在上一个表单的筛选状态下）
        row_count = await _get_assistant_rows(page)
        if row_count > 0:
            print(f"  ⚠️ 未找到下拉框，但检测到 {row_count} 个智能助手，直接使用当前列表")
            return row_count
        
        # 重试一次：等页面完全加载
        print("  ⚠️ 未找到下拉框，等待3秒后重试...")
        await page.wait_for_timeout(3000)
        trigger_pos = await find_dropdown_trigger(page)
        
        if not trigger_pos or not trigger_pos.get('found'):
            # 最终回退：检查列表是否有数据（可能已经是正确状态）
            row_count = await _get_assistant_rows(page)
            if row_count > 0:
                print(f"  ✓ 重试后仍未找到下拉框，但检测到 {row_count} 个智能助手，继续使用")
                return row_count
            
            # 真正无法处理的情况才报错
            print("  ❌ 无法找到右侧「所有本表相关」下拉框！截图 + DOM dump")
            await debug_dump(page, "ERROR_no_right_dropdown", out_dir=d)
            raise Exception("FATAL: 无法找到右侧「所有本表相关」下拉框，请检查页面结构")

    print(f"  [Step 1] 点击右侧下拉框 ({trigger_pos['x']:.0f}, {trigger_pos['y']:.0f})")
    await page.mouse.click(trigger_pos['x'], trigger_pos['y'])
    await page.wait_for_timeout(800)

    print("  [Step 2] 查找下拉选项「作为触发动作」...")
    option_pos = await find_trigger_option(page)

    if not option_pos or not option_pos.get('found'):
        print("  ❌ 无法找到「作为触发动作」选项！")
        if ENABLE_SCREENSHOTS:
            await page.screenshot(path=f"{d}/ERROR_no_option_{ts}.png")
        raise Exception("FATAL: 下拉框已点击，但找不到「作为触发动作」选项")

    print(f"  [Step 2] 点击选项 ({option_pos['x']:.0f}, {option_pos['y']:.0f})")
    await page.mouse.click(option_pos['x'], option_pos['y'])
    print("  ✓ 选择了「作为触发动作」，等待列表刷新...")

    # 等待列表刷新 (v8.0: 从5次改为3次)
    count = 0
    for retry in range(3):
        await page.wait_for_timeout(1000)
        count = await _get_assistant_rows(page)
        print(f"    尝试 {retry+1}/3: 检测到 {count} 个智能助手")
        if count > 0:
            break

    if count == 0:
        print(f"  ⚠️ 等待3秒后仍为0个智能助手")
        if ENABLE_SCREENSHOTS:
            await page.screenshot(path=f"{d}/phase3_0_assistants_{ts}.png", full_page=True)
        return 0

    print(f"  ✓ 筛选后共有 {count} 个本表触发智能助手")
    return count


async def get_assistant_names(page):
    """获取智能助手名称列表（v8.0 清洗版）"""
    rows = await page.query_selector_all('.fx-automation-extension-item')
    assistants = []
    for row in rows:
        # 获取原始文本
        raw_txt = (await row.text_content() or "").strip().replace('\n', ' ')[:120]
        
        # v8.0: 智能提取助手名称（从列表项中提取第一个主要文本）
        clean_txt = await row.evaluate("""
            el => {
                const nameEl = el.querySelector('.name, .title, [class*="name"], h4, h5');
                if (nameEl) return nameEl.textContent.trim();
                
                let text = el.textContent || '';
                const buttons = el.querySelectorAll('button');
                buttons.forEach(btn => {
                    text = text.replace(btn.textContent, '');
                });
                return text.trim();
            }
        """)
        
        if clean_txt:
            txt = clean_txt[:120]
        else:
            txt = raw_txt
        
        # v8.0: 清洗名称并提取触发事件
        name, trigger_event = clean_assistant_name(txt)
        assistants.append({
            'name': name,
            'triggerEvent': trigger_event,
            'rawName': raw_txt[:80]
        })
    return assistants


_JS_FIND_EDIT_BTN_IN_ROW = """
(idx) => {
    const rows = document.querySelectorAll('.fx-automation-extension-item');
    if (!rows[idx]) return null;
    const row = rows[idx];
    const buttons = row.querySelectorAll('button');
    for (const btn of buttons) {
        if (btn.textContent?.trim() === '编辑') return btn;
    }
    const allInRow = row.querySelectorAll('*');
    for (const el of allInRow) {
        if (el.children.length === 0 && el.textContent?.trim() === '编辑') return el;
    }
    return null;
}
"""

_JS_CLICK_EDIT_BTN_IN_ROW = """
(idx) => {
    const rows = document.querySelectorAll('.fx-automation-extension-item');
    if (!rows[idx]) return false;
    const row = rows[idx];
    const buttons = row.querySelectorAll('button');
    for (const btn of buttons) {
        if (btn.textContent?.trim() === '编辑') { btn.click(); return true; }
    }
    const allInRow = row.querySelectorAll('*');
    for (const el of allInRow) {
        if (el.children.length === 0 && el.textContent?.trim() === '编辑') { el.click(); return true; }
    }
    return false;
}
"""


async def find_and_click_edit_button(page, idx):
    """多策略查找并点击编辑按钮"""

    btn_handle = await page.evaluate_handle(_JS_FIND_EDIT_BTN_IN_ROW, idx)

    if btn_handle:
        try:
            await btn_handle.click(timeout=5000)
            print(f"    策略1 [Playwright click] 成功")
            return True
        except Exception:
            pass

    clicked = await page.evaluate(_JS_CLICK_EDIT_BTN_IN_ROW, idx)
    if clicked:
        print(f"    策略2 [JS click] 成功")
        return True

    clicked = await page.evaluate("""
        (idx) => {
            const rows = document.querySelectorAll('.fx-automation-extension-item');
            if (!rows[idx]) return false;
            const rowRect = rows[idx].getBoundingClientRect();
            const allBtns = document.querySelectorAll('button');
            for (const btn of allBtns) {
                if (btn.textContent?.trim() === '编辑') {
                    const r = btn.getBoundingClientRect();
                    if (r.top >= rowRect.top - 5 && r.bottom <= rowRect.bottom + 5) {
                        btn.click(); return true;
                    }
                }
            }
            return false;
        }
    """, idx)
    if clicked:
        print(f"    策略3 [全页匹配] 成功")
        return True

    pos = await page.evaluate("""
        (idx) => {
            const rows = document.querySelectorAll('.fx-automation-extension-item');
            if (!rows[idx]) return null;
            const row = rows[idx];
            const buttons = row.querySelectorAll('button');
            for (const btn of buttons) {
                if (btn.textContent?.trim() === '编辑') {
                    const r = btn.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) return {x: r.left+r.width/2, y: r.top+r.height/2};
                }
            }
            const rr = row.getBoundingClientRect();
            return {x: rr.right - 80, y: rr.top + rr.height / 2};
        }
    """, idx)
    if pos:
        print(f"    策略4 [坐标] ({pos['x']:.0f}, {pos['y']:.0f})")
        await page.mouse.click(pos['x'], pos['y'])
        return True

    return False


async def click_discard_dialog(page):
    """处理弹窗"""
    async def handle_dialog(dialog):
        print(f"    [弹窗] 类型={dialog.type} 消息={str(dialog.message)[:60]}")
        await dialog.dismiss()
    page.on("dialog", handle_dialog)

    for _ in range(10):
        found = await page.evaluate("""
            () => {
                const all = [...document.querySelectorAll("button, [role='button'], span, div")];
                for (const el of all) {
                    const txt = el.textContent?.trim() || '';
                    if (txt === '不保存' || txt === '取消' || txt === 'Discard' || txt === 'Cancel') {
                        el.click(); return txt;
                    }
                }
                return null;
            }
        """)
        if found:
            print(f"    [弹窗] 点击了「{found}」")
            await page.wait_for_timeout(1500)
            break
        await page.wait_for_timeout(200)

    try:
        page.off("dialog", handle_dialog)
    except:
        pass


async def get_workflow_nodes(page):
    """获取工作流节点列表（v8.0 清洗版）"""
    nodes = await page.query_selector_all('.fx-automation-design-node-container')
    result = []
    for i, n in enumerate(nodes):
        raw_text = (await n.text_content() or "").strip().replace('\n', ' ')[:120]
        cls = await n.get_attribute('class') or ""
        
        # v8.0: 智能提取节点名称
        clean_name = await n.evaluate("""
            el => {
                // 尝试找到节点名称元素
                const nameEl = el.querySelector('.node-name, .name, [class*="name"], .title');
                if (nameEl) {
                    let text = nameEl.textContent.trim();
                    // 去除操作按钮文本
                    const actions = ['编辑', '查看', '复制', '删除', '触发'];
                    actions.forEach(action => {
                        text = text.replace(action, '');
                    });
                    return text.trim();
                }
                return el.textContent.trim();
            }
        """)
        
        text = clean_name if clean_name else raw_text
        # 清洗节点名称
        text = clean_node_name(text)

        # 初步识别类型（后续会根据config中的header修正）
        node_type = get_node_type(cls)

        is_fallback_branch = text in _FALLBACK_BRANCH_NAMES or text.startswith(NodeType.FALLBACK)

        result.append({"index": i, "name": text, "type": node_type, "rawType": node_type,
                        "isFallbackBranch": is_fallback_branch})
    return result


async def wait_for_drawer_ready(page, timeout=3000, check_interval=100):
    """等待配置抽屉加载完成（智能等待）"""
    start = asyncio.get_event_loop().time() * 1000
    while (asyncio.get_event_loop().time() * 1000 - start) < timeout:
        drawer = await page.query_selector('.fx-automation-node-config-drawer')
        if drawer:
            header = await page.query_selector('.fx-automation-node-config-drawer .header, .fx-automation-node-config-drawer [class*="header"]')
            if header:
                return True
        await page.wait_for_timeout(check_interval)
    return False


async def extract_node_config(page, include_empty=False):
    config = await page.evaluate("""
        (opts) => {
            const includeEmpty = opts.includeEmpty;
            const drawer = document.querySelector('.fx-automation-node-config-drawer');
            if (!drawer) return null;
            const result = {
                header: '',
                fields: [],
                mappings: [],
                branchRule: '',       // [新增] 条件分支节点的执行规则
                drawerScrollable: null,
                drawerScrollTop: 0,
                drawerScrollHeight: 0
            };

            // --- Header ---
            const headerEl = drawer.querySelector('.header, .drawer-header, [class*="header"]');
            result.header = headerEl ? headerEl.textContent?.trim() || '' : '';

            // --- [新增] 分支执行规则提取 ---
            // 简道云条件分支节点的右侧抽屉包含：
            //   「分支执行规则」> 单选按钮:
            //     - 满足条件的分支都执行 (radio checked)
            //     - 只执行满足条件的第一个分支 (radio unchecked)
            //
            // DOM结构大致为：
            //   div.flat-item > ... > label > input[type=radio][checked] + span > 文本
            
            const ruleLabels = drawer.querySelectorAll('label');
            for (const label of ruleLabels) {
                const labelText = label.textContent?.trim() || '';
                
                // 匹配分支执行规则相关的label文本
                if (
                    labelText.includes('满足条件') && 
                    (labelText.includes('都执行') || labelText.includes('第一个'))
                ) {
                    const radio = label.querySelector('input[type="radio"], input[type="checkbox"]');
                    const isChecked = !!radio && radio.checked;
                    result.branchRule = isChecked 
                        ? `✅ ${labelText}`  // 当前选中的规则
                        : `○ ${labelText}`;    // 未选中的备选项
                    
                    // 也收集到 fields 中保持兼容
                    result.fields.push({
                        title: '分支执行规则',
                        body: isChecked ? `${labelText}（已选中）` : `${labelText}（未选中）`,
                        checked: isChecked
                    });
                    
                    // 如果已找到选中项，记录当前生效的完整规则描述
                    if (isChecked) {
                        result.branchRuleActive = labelText;
                    }
                }
            }

            // 备选：通过文本匹配整个抽屉内容
            if (!result.branchRule) {
                const drawerText = drawer.textContent || '';
                if (drawerText.includes('分支执行规则') || drawerText.includes('满足条件')) {
                    result.branchRule = '[检测到分支节点但未解析出具体规则]';
                    // 尝试从抽屉文本中提取
                    const match = drawerText.match(/满足条件[^。]*?都执行|只执行[^。]*?第一个/g);
                    if (match) {
                        result.branchRuleCandidates = match;
                    }
                }
            }

            // === [v8.0.7] 优化flat-item字段提取 ===
            // 改进点：
            // 1. 排除UI图标（垃圾桶等）
            // 2. 查询条件结构化提取（关系、多个条件、结果条数）
            // 3. 为不同组件添加分隔符
            const items = drawer.querySelectorAll('.flat-item');
            items.forEach(item => {
                // 排除图标元素
                const icons = item.querySelectorAll('.iconfont, .x-icon, [class*="icon"], .remove-btn, .add-btn, .fx-icon');
                icons.forEach(icon => icon.remove());
                
                const titles = item.querySelectorAll('.node-attr-title, .flat-item-header, [class*="title"]');
                let title = '';
                titles.forEach(t => {
                    const t2 = t.textContent?.trim() || '';
                    if (t2 && !title) title = t2;
                });
                
                let body = '';
                
                // v8.0.8: 特殊处理查询条件 - 直接在flat-item中查找
                if (title.includes('查询条件')) {
                    // 提取条件关系（所有/任一）
                    const relationEl = item.querySelector('.fx-filter-relation');
                    let relationText = '';
                    if (relationEl) {
                        const prefix = relationEl.querySelector('.prefix-text')?.textContent?.trim() || '';
                        const selector = relationEl.querySelector('.filter-selector, .selector-text')?.textContent?.trim() || '';
                        const suffix = relationEl.querySelector('.suffix-text')?.textContent?.trim() || '';
                        relationText = `${prefix}${selector}${suffix}`;
                    }
                    
                    // 提取每个条件 - 直接在flat-item中查找.cond-item
                    const condItems = item.querySelectorAll('.cond-item');
                    const conditions = [];
                    condItems.forEach((cond, idx) => {
                        // 提取字段名
                        const fieldEl = cond.querySelector('.fx-filter-item, .cond-item-left, [class*="filter-item"]');
                        const fieldName = fieldEl ? fieldEl.textContent.trim() : '';
                        
                        // 提取操作符
                        const methodEl = cond.querySelector('.method-area, [class*="method"]');
                        const method = methodEl ? methodEl.textContent.trim() : '';
                        
                        // 提取值
                        const valueEl = cond.querySelector('.value-area, .node-and-value, [class*="value"]');
                        let value = '';
                        if (valueEl) {
                            // 排除图标
                            const valIcons = valueEl.querySelectorAll('.iconfont, .x-icon, [class*="icon"]');
                            valIcons.forEach(vi => vi.remove());
                            value = valueEl.textContent.trim();
                        }
                        
                        if (fieldName && method) {
                            // v8.0.9: 使用特殊分隔符 ||| 分隔 A X B 三部分
                            conditions.push(`${fieldName}|||${method}|||${value}`);
                        }
                    });
                    
                    // 构建查询条件body
                    if (relationText && conditions.length > 0) {
                        body = `${relationText}；${conditions.join('；')}`;
                    } else if (conditions.length > 0) {
                        body = conditions.join('；');
                    } else {
                        // v8.0.8: DOM提取失败，回退到文本解析
                        // 尝试从body中提取条件
                        const rawBody = (item.textContent || '').trim();
                        const condMatch = rawBody.match(/查询出的数据([\s\S]*?)(?=\*?查询结果条数|$)/);
                        if (condMatch) {
                            body = condMatch[1].trim();
                            // 清理噪音
                            body = body.replace(/删除/g, '').replace(/添加/g, '').replace(/\s+/g, ' ');
                        }
                    }
                    
                    console.log(`[v8.0.8] 查询条件提取: 关系="${relationText}", 条件数=${conditions.length}`);
                }
                
                // v8.0.8: 特殊处理查询结果条数
                if (title.includes('查询结果条数')) {
                    const inputEl = item.querySelector('input[type="number"], .input-inner');
                    if (inputEl) {
                        const value = inputEl.value || inputEl.textContent?.trim() || '';
                        if (value) body = value + '条';
                    }
                }
                
                // v8.0.8: 特殊处理修改/删除节点的筛选条件
                if (title.includes('筛选') || title.includes('删除条件')) {
                    const condItems = item.querySelectorAll('.cond-item');
                    const conditions = [];
                    condItems.forEach((cond) => {
                        const fieldEl = cond.querySelector('.fx-filter-item, .cond-item-left, [class*="filter-item"]');
                        let fieldName = fieldEl ? fieldEl.textContent.trim() : '';

                        const methodEl = cond.querySelector('.method-area, [class*="method"]');
                        const method = methodEl ? methodEl.textContent.trim() : '';

                        const valueEl = cond.querySelector('.value-area, .node-and-value, [class*="value"]');
                        let value = '';
                        if (valueEl) {
                            const valIcons = valueEl.querySelectorAll('.iconfont, .x-icon, [class*="icon"]');
                            valIcons.forEach(vi => vi.remove());
                            value = valueEl.textContent.trim();
                        }

                        if (fieldName && method) {
                            // v8.0.9: 如果fieldName包含method和value，先清理
                            // 例如 "生产计划等于触发数据—数据ID" 需要清理成 "生产计划"
                            let cleanFieldName = fieldName;
                            if (value && cleanFieldName.endsWith(value)) {
                                cleanFieldName = cleanFieldName.slice(0, -value.length).trim();
                            }
                            if (method && cleanFieldName.endsWith(method)) {
                                cleanFieldName = cleanFieldName.slice(0, -method.length).trim();
                            }
                            // v8.0.9: 使用特殊分隔符 ||| 分隔 A X B 三部分
                            conditions.push(`${cleanFieldName}|||${method}|||${value}`);
                        }
                    });

                    if (conditions.length > 0) {
                        body = conditions.join('；');
                    }
                }

                // v8.0.9: 特殊处理分支条件的条件设置（类似查询条件）
                if (title.includes('条件设置')) {
                    // 提取分支执行规则（满足所有/任一条件执行本分支）
                    // 从截图看DOM结构是 div.fx-filter-relation > 满足 所有 条件，执行本分支
                    let ruleText = '';
                    
                    // 方法1: 优先从 .fx-filter-relation 元素提取（最准确）
                    const relationEl = item.querySelector('.fx-filter-relation');
                    if (relationEl) {
                        ruleText = relationEl.textContent?.trim() || '';
                    }
                    
                    // 方法2: 从整个item文本中用正则提取
                    if (!ruleText) {
                        const itemText = item.textContent || '';
                        const ruleMatch = itemText.match(/满足\s*(所有|任一)\s*条件[，,]?\s*执行本分支/);
                        if (ruleMatch) {
                            ruleText = ruleMatch[0];
                        }
                    }
                    
                    // 方法3: 尝试其他选择器
                    if (!ruleText) {
                        const ruleEl2 = item.querySelector('.branch-rule, [class*="branch-rule"], .relation-text, [class*="relation"], .selector-text');
                        if (ruleEl2) {
                            ruleText = ruleEl2.textContent?.trim() || '';
                        }
                    }

                    // 提取每个条件 - 直接在flat-item中查找.cond-item
                    const condItems = item.querySelectorAll('.cond-item');
                    const conditions = [];
                    condItems.forEach((cond) => {
                        // 提取字段名 - 使用更精确的选择器
                        const fieldEl = cond.querySelector('.fx-filter-item, .cond-item-left, [class*="filter-item"], .field-name');
                        let fieldName = fieldEl ? fieldEl.textContent.trim() : '';

                        // 提取操作符
                        const methodEl = cond.querySelector('.method-area, [class*="method"], .operator');
                        const method = methodEl ? methodEl.textContent.trim() : '';

                        // 提取值 - 使用更精确的选择器
                        const valueEl = cond.querySelector('.value-area, .node-and-value, [class*="value"], .cond-item-right');
                        let value = '';
                        if (valueEl) {
                            const valIcons = valueEl.querySelectorAll('.iconfont, .x-icon, [class*="icon"]');
                            valIcons.forEach(vi => vi.remove());
                            value = valueEl.textContent.trim();
                        }

                        if (fieldName && method) {
                            // v8.0.9: 如果fieldName包含method和value，先清理
                            // 例如 "触发数据—生产计划状态等于任意一个待执行物料分析" 
                            // 应该清理成 "触发数据—生产计划状态"，value应该是"待执行物料分析"
                            let cleanFieldName = fieldName;
                            let cleanValue = value;
                            
                            // 检查fieldName是否包含method
                            const methodIdx = fieldName.indexOf(method);
                            if (methodIdx > 0) {
                                // fieldName包含method，说明fieldName是完整的"字段名+操作符+值"
                                // 从fieldName中解析
                                cleanFieldName = fieldName.substring(0, methodIdx).trim();
                                const afterMethod = fieldName.substring(methodIdx + method.length).trim();
                                // afterMethod就是值
                                if (afterMethod && (!cleanValue || cleanValue === cleanFieldName)) {
                                    cleanValue = afterMethod;
                                }
                            } else {
                                // fieldName不包含method，使用原来的清理逻辑
                                if (value && cleanFieldName.endsWith(value)) {
                                    cleanFieldName = cleanFieldName.slice(0, -value.length).trim();
                                }
                                if (method && cleanFieldName.endsWith(method)) {
                                    cleanFieldName = cleanFieldName.slice(0, -method.length).trim();
                                }
                            }
                            
                            // v8.0.9: 使用特殊分隔符 ||| 分隔 A X B 三部分
                            conditions.push(`${cleanFieldName}|||${method}|||${cleanValue}`);
                        }
                    });

                    // 构建分支条件body: 规则前缀 + 分隔符 + 条件列表
                    if (conditions.length > 0) {
                        if (ruleText) {
                            body = `${ruleText} ｜ ${conditions.join(' ｜ ')}`;
                        } else {
                            body = conditions.join(' ｜ ');
                        }
                    }
                }
                
                // 默认提取方式
                if (!body) {
                    body = (item.textContent || '').trim();
                    if (title) body = body.replace(title, '').trim();
                    
                    // 排除"删除"等UI文本
                    body = body.replace(/删除/g, '').replace(/添加/g, '').replace(/清空/g, '');
                    
                    // 为查询条件添加分隔符
                    body = body.replace(/查询出的数据/g, ' | 查询出的数据');
                    
                    // 清理多余空格
                    body = body.replace(/\s+/g, ' ').trim();
                    
                    // 添加组件分隔符
                    body = body.replace(/排序规则/g, ' | 排序规则');
                }
                
                if (title && body) result.fields.push({title, body});
            });

            // === [v8.0.7] 优化config-item映射 ===
            const configItems = drawer.querySelectorAll('.config-item');
            configItems.forEach(ci => {
                // 排除图标元素
                const icons = ci.querySelectorAll('.iconfont, .x-icon, [class*="icon"], .remove-btn, .add-btn');
                icons.forEach(icon => icon.remove());
                
                let text = (ci.textContent || '').trim();
                // 排除UI噪音
                text = text.replace(/删除/g, '').replace(/添加/g, '').replace(/清空/g, '');
                text = text.replace(/\s+/g, ' ').trim();
                
                // 添加组件分隔符
                text = text.replace(/排序规则/g, ' | 排序规则');
                text = text.replace(/筛选/g, ' | 筛选');
                
                if (text && text.length > 2) result.mappings.push(text);
            });

            // === [v8.0.9] 结构化字段映射解析 - 基于DOM直接提取 ===
            // 支持新增(create)和修改(update)两种节点
            // 基于简道云DOM结构：
            // - 新增字段映射区域：.fx-automation-design-create-field-set
            // - 修改字段映射区域：.fx-automation-design-update-field-set
            // - 每行：.rel-item
            // - 字段名：.rel-item-field
            // - 值类型+值详情：.node-and-value 或 .node-field-value
            // - 未设置占位符：.placeholder
            
            result.fieldMappings = [];
            
            // v8.0.9: 支持新增和修改两种节点的选择器
            const fieldSet = drawer.querySelector(
                '.fx-automation-design-create-field-set, ' +
                '.fx-automation-design-update-field-set, ' +
                '.fx-automation-design-modify-field-set, ' +
                '[class*="create-field-set"], ' +
                '[class*="update-field-set"], ' +
                '[class*="modify-field-set"]'
            );
            if (fieldSet) {
                const relItems = fieldSet.querySelectorAll('.rel-item');
                console.log(`[v8.0.9] 找到 ${relItems.length} 个字段映射行`);
                
                relItems.forEach((item, idx) => {
                    // 提取字段名
                    const fieldEl = item.querySelector('.rel-item-field');
                    const fieldName = fieldEl ? fieldEl.textContent.trim() : '';
                    
                    // v8.1.0: 提取值区域 - 支持多种DOM结构
                    // 自定义模式下容器类为 .mode-and-value，不是 .node-and-value
                    const valueEl = item.querySelector(
                        '.node-and-value, .node-field-value, .mode-and-value'
                    );

                    // 检查是否有placeholder（未设置）
                    const placeholderEl = valueEl ? valueEl.querySelector('.placeholder') : null;
                    if (placeholderEl) {
                        console.log(`[v8.1.0] 行${idx}: ${fieldName} = 未设置`);
                        if (includeEmpty) {
                            result.fieldMappings.push({
                                field: fieldName,
                                sourceType: 'empty',
                                customValue: '',
                                sourceNode: '',
                                sourceField: ''
                            });
                        }
                        return;
                    }

                    // v8.1.0: 检测值类型（节点字段值/自定义/空值）
                    let sourceType = 'unknown';
                    let customValue = '';
                    let sourceNode = '';
                    let sourceField = '';

                    // 方法1: 直接在 rel-item 层找 input.input-inner（自定义模式的实际路径）
                    // DOM: .rel-item > .mode-and-value > .value-wrapper > .value-widget > .x-input > .x-inner-wrapper > input.input-inner
                    const directInput = item.querySelector('input.input-inner, input.input-text');

                    // 方法2: 检查是否有 fx-custom-combo 类（自定义类型的标识）
                    const isCustomCombo = (valueEl && (
                        valueEl.classList.contains('fx-custom-combo') ||
                        valueEl.querySelector('.fx-custom-combo') !== null
                    )) || directInput !== null;

                    console.log(`[v8.1.0] 行${idx}: ${fieldName} isCustom=${isCustomCombo} hasDirectInput=${!!directInput} hasValueEl=${!!valueEl}`);

                    if (isCustomCombo) {
                        sourceType = 'custom';

                        // 优先使用直接找到的 input（值最准确）
                        if (directInput) {
                            customValue = directInput.value || directInput.getAttribute('value') || '';
                            if (!customValue) {
                                customValue = directInput.textContent?.trim() || '';
                            }
                        }

                        // 降级：从 valueEl 下查找 input
                        if (!customValue && valueEl) {
                            const inputInValueEl = valueEl.querySelector(
                                '.input-inner, .input-text, input[type="text"], input:not([type])'
                            );
                            if (inputInValueEl) {
                                customValue = inputInValueEl.value || inputInValueEl.getAttribute('value') || '';
                            }
                        }

                        // 最终降级：从文本内容提取
                        if (!customValue && valueEl) {
                            const contentEl = valueEl.querySelector('.value-content, .node-field-content');
                            if (contentEl) {
                                customValue = contentEl.textContent.trim();
                                customValue = customValue.replace(/自定义/g, '').replace(/节点字段值/g, '').replace(/=/g, '').trim();
                            } else {
                                customValue = valueEl.textContent.replace(/自定义/g, '').replace(/节点字段值/g, '').replace(/=/g, '').trim();
                            }
                        }

                        console.log(`[v8.1.0] 行${idx}: ${fieldName} = [custom] "${customValue}"`);
                    } else if (!valueEl) {
                        console.log(`[v8.1.0] 行${idx}: ${fieldName} - 未找到值区域，跳过`);
                        return;
                    } else {
                        const contentEl = valueEl.querySelector('.node-field-content');
                        let valueText = '';
                        
                        if (contentEl) {
                            const textSpan = contentEl.querySelector('span:not(.iconfont):not(.x-icon)');
                            valueText = textSpan ? textSpan.textContent.trim() : contentEl.textContent.trim();
                        } else {
                            valueText = valueEl.textContent.trim();
                        }
                        
                        valueText = valueText.replace(/[\u200b\u200c\u200d\ufeff]/g, '').trim();
                        
                        if (!fieldName || !valueText) return;
                        
                        if (valueText.includes('—') || valueText.includes('-')) {
                            sourceType = 'node';
                            const dashIndex = valueText.search(/[—-]/);
                            sourceNode = valueText.substring(0, dashIndex).trim();
                            sourceField = valueText.substring(dashIndex + 1).trim();
                        } else if (valueText === '空值' || valueText === '') {
                            sourceType = 'empty';
                        } else {
                            sourceType = 'custom';
                            customValue = valueText;
                        }
                        
                        console.log(`[v8.0.9] 行${idx}: ${fieldName} = [${sourceType}] ${customValue || (sourceNode + '→' + sourceField)}`);
                    }
                    
                    const mapping = { 
                        field: fieldName,
                        sourceType: sourceType,
                        customValue: customValue,
                        sourceNode: sourceNode,
                        sourceField: sourceField
                    };
                    
                    result.fieldMappings.push(mapping);
                });
            }
            
            // 方法2: 如果DOM提取失败或结果无效，回退到文本解析（v8.1 逻辑，用于兼容）
            // 检测 fieldMappings 是否包含有效映射（有 sourceNode+sourceField 或非空 customValue）
            const hasValidMappings = result.fieldMappings.some(fm => 
                (fm.sourceType === 'node' && fm.sourceNode && fm.sourceField) ||
                (fm.sourceType === 'custom' && fm.customValue) ||
                (fm.sourceType === 'empty')
            );
            if (!hasValidMappings) {
                console.log('[v8.2] DOM提取未找到有效字段映射，回退到文本解析');
                
                // 清空无效的 DOM 提取结果
                result.fieldMappings = [];
                
                const fieldValueItem = result.fields.find(f => 
                    f.title && (f.title.includes('设置字段值') || f.title.includes('*设置字段值'))
                );
                
                if (fieldValueItem && fieldValueItem.body) {
                    let rawText = fieldValueItem.body;
                    const uiButtons = ['快捷填充', '添加字段', '添加条件', '添加排序规则', '清空'];
                    for (const btn of uiButtons) {
                        rawText = rawText.replace(new RegExp(btn, 'g'), '');
                    }
                    rawText = rawText.replace(/[\u200b\u200c\u200d\ufeff]/g, '').trim();
                    
                    // v8.2: 改进的字段映射解析算法
                    // 格式：字段名=值字段名=值...（无分隔符）
                    // 值类型：
                    //   - "字段" → 空值/未设置
                    //   - "节点名—字段名" → 节点字段值
                    //   - 其他 → 自定义值
                    
                    // 已知字段名列表（用于辅助识别字段边界）
                    const knownFieldNames = [
                        '生产计划编号', '生产计划状态', '生产计划来源', '销售订单', '销售数量',
                        '销售订单编号', '销售订单名称', '生产产品', '产品BOM', '产品批次号',
                        '计划生产数量', '当前库存数量', '实际所需生产数量', '交货日期',
                        '计划开工日期', '计划完工日期', '产品名称', '产品编码', '获取方式',
                        '规格型号', '生产计划', '生产计划明细编号', '备注', '创建时间',
                        '更新时间', '创建人', '更新人', '状态', '名称', '编号', '类型',
                        '数量', '金额', '日期', '时间', '描述', '说明', '结果', '原因'
                    ];
                    
                    // 按长度降序排列，优先匹配更长的字段名
                    knownFieldNames.sort((a, b) => b.length - a.length);
                    
                    // 第一步：找到所有已知字段名的位置
                    const fieldPositions = [];
                    for (const fn of knownFieldNames) {
                        let searchPos = 0;
                        while (true) {
                            const idx = rawText.indexOf(fn + '=', searchPos);
                            if (idx === -1) break;
                            // 检查是否已被更长的字段名包含
                            const isSubField = fieldPositions.some(fp => 
                                idx > fp.index && idx < fp.index + fp.name.length
                            );
                            if (!isSubField) {
                                fieldPositions.push({ index: idx, name: fn });
                            }
                            searchPos = idx + 1;
                        }
                    }
                    
                    // 按位置排序
                    fieldPositions.sort((a, b) => a.index - b.index);
                    
                    // 第二步：提取每个字段的值
                    for (let i = 0; i < fieldPositions.length; i++) {
                        const fp = fieldPositions[i];
                        const fieldName = fp.name;
                        const valueStart = fp.index + fieldName.length + 1;
                        const valueEnd = (i + 1 < fieldPositions.length) 
                            ? fieldPositions[i + 1].index 
                            : rawText.length;
                        const valueContent = rawText.substring(valueStart, valueEnd).trim();
                        
                        const mapping = { 
                            field: fieldName, 
                            sourceType: 'unknown', 
                            customValue: '', 
                            sourceNode: '', 
                            sourceField: '' 
                        };
                        
                        // 解析值
                        if (!valueContent || valueContent === '字段' || valueContent.startsWith('请选择')) {
                            mapping.sourceType = 'empty';
                        } else if (valueContent.includes('—')) {
                            // 节点字段值：节点名—字段名
                            mapping.sourceType = 'node';
                            const dashIndex = valueContent.indexOf('—');
                            mapping.sourceNode = valueContent.substring(0, dashIndex).trim();
                            mapping.sourceField = valueContent.substring(dashIndex + 1).trim();
                        } else if (valueContent.includes('-')) {
                            // 兼容英文连字符
                            mapping.sourceType = 'node';
                            const dashIndex = valueContent.indexOf('-');
                            mapping.sourceNode = valueContent.substring(0, dashIndex).trim();
                            mapping.sourceField = valueContent.substring(dashIndex + 1).trim();
                        } else {
                            // 自定义值
                            mapping.sourceType = 'custom';
                            mapping.customValue = valueContent;
                        }
                        
                        result.fieldMappings.push(mapping);
                        console.log(`[v8.2] 文本解析: ${fieldName} = [${mapping.sourceType}] ${mapping.sourceNode}→${mapping.sourceField || mapping.customValue}`);
                    }
                    
                    // 第三步：如果已知字段名列表没有匹配到任何字段，尝试通用正则匹配
                    if (fieldPositions.length === 0) {
                        console.log('[v8.2] 未匹配到已知字段名，尝试通用正则');
                        const fieldPattern = /([一-龥a-zA-Z0-9_]{2,15})=/g;
                        const matches = [...rawText.matchAll(fieldPattern)];
                        
                        for (let i = 0; i < matches.length; i++) {
                            const fieldName = matches[i][1];
                            const valueStart = matches[i].index + fieldName.length + 1;
                            const valueEnd = (i + 1 < matches.length) ? matches[i + 1].index : rawText.length;
                            const valueContent = rawText.substring(valueStart, valueEnd).trim();
                            
                            if (!fieldName || fieldName.length < 2) continue;
                            if (['查询', '请选择', '满足', '添加', '快捷填充', '条件', '字段'].includes(fieldName)) continue;
                            
                            const mapping = { 
                                field: fieldName, 
                                sourceType: 'unknown', 
                                customValue: '', 
                                sourceNode: '', 
                                sourceField: '' 
                            };
                            
                            if (!valueContent || valueContent === '字段' || valueContent.startsWith('请选择')) {
                                mapping.sourceType = 'empty';
                            } else if (valueContent.includes('—')) {
                                mapping.sourceType = 'node';
                                const dashIndex = valueContent.indexOf('—');
                                mapping.sourceNode = valueContent.substring(0, dashIndex).trim();
                                mapping.sourceField = valueContent.substring(dashIndex + 1).trim();
                            } else if (valueContent.includes('-')) {
                                mapping.sourceType = 'node';
                                const dashIndex = valueContent.indexOf('-');
                                mapping.sourceNode = valueContent.substring(0, dashIndex).trim();
                                mapping.sourceField = valueContent.substring(dashIndex + 1).trim();
                            } else {
                                mapping.sourceType = 'custom';
                                mapping.customValue = valueContent;
                            }
                            
                            result.fieldMappings.push(mapping);
                        }
                    }
                }
            }

            // --- 滚动信息 ---
            const scrollable = drawer.querySelector('.drawer-content, .config-content, .node-config-body, [class*="content"], [class*="body"]');
            if (scrollable) {
                result.drawerScrollable = scrollable.className;
                result.drawerScrollTop = scrollable.scrollTop;
                result.drawerScrollHeight = scrollable.scrollHeight;
            }
            return result;
        }
    """, {'includeEmpty': include_empty})
    return config


async def scroll_and_screenshot_drawer(page, base, node_idx):
    """滚动并截图配置抽屉（v8.0.6: 受 ENABLE_SCREENSHOTS 控制，默认关闭）"""
    if not ENABLE_SCREENSHOTS:
        return []
    shots = []
    drawer = await page.query_selector('.fx-automation-node-config-drawer')
    if not drawer:
        return shots

    scrollable = await page.query_selector('.fx-automation-node-config-drawer .drawer-content, .fx-automation-node-config-drawer .config-content')
    if not scrollable:
        scrollable = drawer

    info = await scrollable.evaluate("el => ({ sh: el.scrollHeight, ch: el.clientHeight })")
    if info['sh'] <= info['ch']:
        p = f"{base}_n{node_idx:02d}_drawer.png"
        await page.screenshot(path=p, full_page=False)
        shots.append(p)
        return shots

    sy = 0
    while sy < info['sh']:
        await scrollable.evaluate(f"el => el.scrollTop = {sy}")
        await page.wait_for_timeout(500)
        p = f"{base}_n{node_idx:02d}_drawer{len(shots):02d}.png"
        await page.screenshot(path=p, full_page=False)
        shots.append(p)
        sy += info['ch']

    await scrollable.evaluate("el => el.scrollTop = 0")
    await page.wait_for_timeout(300)
    return shots


async def ensure_drawer_closed(page):
    """强制关闭右侧配置抽屉。

    问题背景：
    - 某些节点（分支条件、循环容器末尾）点击后，上一个节点的配置抽屉不会自动关闭
    - 此时抽屉遮挡画布，导致后续节点点击超时
    - 单纯按 Escape 有时关不掉

    解决策略：
    1. 先按 Escape 尝试关闭
    2. 如果还在，用 JS 直接隐藏/移除抽屉 DOM
    3. 重新查询节点列表（抽屉关闭后画布可能重渲染）
    """
    # Step 1: Escape 尝试关闭
    await page.keyboard.press('Escape')
    await page.wait_for_timeout(400)

    # Step 2: 检查抽屉是否还存在
    still_visible = await page.evaluate("""
        () => {
            const d = document.querySelector('.fx-automation-node-config-drawer');
            if (!d) return false;
            // 检查是否有 visible 类
            return d.classList.contains('visible') || d.offsetHeight > 0;
        }
    """)

    if not still_visible:
        return

    # Step 3: 用 JS 强制隐藏抽屉（不点击页面，避免破坏画布）
    await page.evaluate("""
        () => {
            const d = document.querySelector('.fx-automation-node-config-drawer');
            if (d) {
                d.classList.remove('visible');
                d.style.display = 'none';
            }
        }
    """)
    await page.wait_for_timeout(300)


async def extract_single_assistant(page, idx, name, output_dir=None, screenshots_dir=None, docs_dir=None):
    """
    采集单个智能助手的画布+配置（v8.0 优化版）。
    
    v8.0 改进：
    - 节点名称智能清洗
    - 类型从 header 修正
    - 业务分类自动标记
    - 截图策略优化（只保留关键节点）
    """
    # v8.0: 支持分离的文档和截图目录
    if docs_dir is None:
        docs_dir = output_dir or OUTPUT_DIR
    if screenshots_dir is None:
        screenshots_dir = output_dir or OUTPUT_DIR
        
    # v8.0: 简化文件名，只使用索引和时间戳，避免名称过长
    ts = datetime.now().strftime('%H%M%S')
    base_name = f"a{idx:02d}_{ts}"
    
    # 截图路径（放在截图目录）
    screenshot_base = f"{screenshots_dir}/{base_name}"
    # 文档路径（放在文档目录）
    doc_base = f"{docs_dir}/{base_name}"

    result = {
        "index": idx,
        "name": name,
        "url": page.url,
        "nodes": [],
        "captured_at": datetime.now().isoformat(),
        "screenshots": [],
        "businessType": "",  # v8.0: 业务类型
        "businessDescription": "",
        "confidence": 0,
    }

    print(f"\n  处理智能助手 [{idx+1}]: {name[:60]}")

    await click_discard_dialog(page)
    
    # v8.0.6: 只保留1张页面总览截图（受 ENABLE_SCREENSHOTS 控制）
    if ENABLE_SCREENSHOTS:
        page_p = f"{screenshot_base}_page.png"
        await page.screenshot(path=page_p, full_page=True)
        result['screenshots'].append(page_p)

    nodes = await get_workflow_nodes(page)
    result['nodes'] = nodes
    print(f"    节点数: {len(nodes)}")

    if not nodes:
        print("    ⚠️ 未找到节点")
        # v8.0: 生成精简报告
        result.update(classify_assistant_business_type(result))
        with open(f"{doc_base}_result.json", 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return result

    # v8.0.9: 画布滚动截图（受 ENABLE_SCREENSHOTS 控制，关闭时跳过）
    canvas = await page.query_selector('.fx-automation-canvas, .fx-automation-design-canvas')
    if canvas and ENABLE_SCREENSHOTS:
        info = await canvas.evaluate("el => ({ sh: el.scrollHeight, ch: el.clientHeight })")
        sy = 0
        cv_count = 0
        max_canvas_shots = 3
        while sy < info['sh'] and cv_count < max_canvas_shots:
            await canvas.evaluate(f"el => el.scrollTop = {sy}")
            await page.wait_for_timeout(600)
            p = f"{screenshot_base}_cv{cv_count:02d}.png"
            await canvas.screenshot(path=p)
            result['screenshots'].append(p)
            sy += 800
            cv_count += 1
        await canvas.evaluate("el => el.scrollTop = 0")
        await page.wait_for_timeout(300)
        print(f"    画布截图: {cv_count}张")

    # 逐节点配置（v8.0: 智能截图策略）
    captured_nodes = 0
    for i, node in enumerate(nodes):
        print(f"    节点 {i+1}/{len(nodes)}: {node['name'][:40]}")
        try:
            # v8.1.0: 跳过"其他条件"兜底分支节点 —— 点击后不会打开抽屉，
            # 若不跳过会错误复用上一个节点的抽屉数据
            if node.get('isFallbackBranch'):
                node['type'] = NodeType.FALLBACK
                node['config'] = {
                    'header': NodeType.FALLBACK,
                    'note': '默认兜底分支：所有条件都不满足时执行本分支，无需配置',
                    'fields': [], 'mappings': [], 'fieldMappings': []
                }
                print(f"      ⏭️ 跳过兜底分支节点（{NodeType.FALLBACK}）")
                continue

            # 强制关闭上一个节点的配置抽屉，防止遮挡导致点击超时
            await ensure_drawer_closed(page)

            # 每次循环重新查询节点，避免 DOM 引用过期
            all_nodes = await page.query_selector_all('.fx-automation-design-node-container')
            if i < len(all_nodes):
                await all_nodes[i].click()
            else:
                print(f"      ⚠️ 节点索引 {i} 超出范围({len(all_nodes)})，跳过")
                continue
            
            # v8.0.9: 智能等待配置抽屉加载完成（替代固定1500ms延时）
            drawer_ready = await wait_for_drawer_ready(page)
            if not drawer_ready:
                print(f"      ⚠️ 配置抽屉加载超时，使用备用延时")
                await page.wait_for_timeout(1000)

            config = await extract_node_config(page, include_empty=INCLUDE_EMPTY_FIELD_MAPPINGS)
            
            # v8.0: 使用 header 修正节点类型
            if config and config.get('header'):
                corrected_type = get_node_type_from_header(config['header'], '')
                if corrected_type != NodeType.UNKNOWN:
                    node['type'] = corrected_type
            
            # v8.0: 清理 config 中的 UI 噪音
            config = clean_config_noise(config)
            
            node['config'] = config
            f_count = len(config.get('fields', [])) if config else 0
            m_count = len(config.get('mappings', [])) if config else 0
            
            # 显示分支规则（如果有）
            branch_rule = ''
            if config:
                br_active = config.get('branchRuleActive', '')
                br = config.get('branchRule', '')
                if br_active or br:
                    branch_rule = f" | 分支规则: {br_active or br[:30]}..."
            
            print(f"      类型={node['type']} 配置={f_count} 映射={m_count}{branch_rule}")

            # v8.0.6: 智能截图决策（受 ENABLE_SCREENSHOTS 控制）
            should_capture = ENABLE_SCREENSHOTS and should_capture_node_screenshot(node['type'], config)
            if should_capture:
                config_p = f"{screenshot_base}_n{i:02d}_{node['type']}.png"
                await page.screenshot(path=config_p, full_page=False)
                result['screenshots'].append(config_p)
                captured_nodes += 1
                print(f"      📸 已截图（复杂节点）")
            
        except Exception as e:
            print(f"      ⚠️ 节点失败: {e}")
            # 失败后强制关闭可能残留的抽屉
            await ensure_drawer_closed(page)

    # v8.0: 业务分类
    business_info = classify_assistant_business_type(result)
    result.update(business_info)
    print(f"    🏷️ 业务类型: {result['businessType']} (置信度{result['confidence']}%)")

    # v8.0: 清理截图路径，只保留文件名（从完整路径改为相对路径）
    result['screenshots'] = [os.path.basename(p) for p in result['screenshots']]
    
    # 保存 JSON（放在文档目录）
    with open(f"{doc_base}_result.json", 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"    ✅ 完成: {captured_nodes}个节点截图, 业务类型={result['businessType']}")

    return result


async def go_back_to_list(page):
    """返回智能助手列表"""
    await click_discard_dialog(page)

    back_btn = await page.query_selector('.fx-navigation-bar-back-btn')
    if back_btn:
        await back_btn.click()
        await page.wait_for_timeout(2000)
    else:
        await page.goto(f"https://www.jiandaoyun.com/dashboard/app/{APP_ID}/form/{FORM_ID}/edit#/extension/trigger")
        await page.wait_for_timeout(2500)

    await click_discard_dialog(page)


async def navigate_to_form(page, form_id, form_name):
    """导航到指定表单的智能助手列表"""
    global FORM_ID, FORM_NAME
    FORM_ID = form_id
    FORM_NAME = form_name

    target_url = f"https://www.jiandaoyun.com/dashboard/app/{APP_ID}/form/{form_id}/edit#/extension/trigger"

    print(f"\n  → 导航到: {form_name} (ID: {form_id[:8]}...)")

    state, _ = await get_url_state(page)

    # 如果已在正确的表单智能助手页面
    if form_id in page.url and 'extension/trigger' in page.url:
        print(f"  ✓ 已在目标表单的智能助手列表")
        return True

    # 如果在同一个表单的其他页面
    if form_id in page.url:
        await page.goto(target_url)
        await page.wait_for_timeout(3000)
        print(f"  ✓ 同表单内切换到智能助手")
        return True

    # 需要从其他位置导航过来
    await page.goto(target_url)
    await page.wait_for_timeout(4000)

    # 验证到达
    if 'extension' in page.url or 'trigger' in page.url:
        print(f"  ✓ 成功到达智能助手列表")
        return True

    # 可能需要先进入编辑模式再切tab
    if '/edit#' in page.url:
        # 点击扩展功能 tab
        ext_tab = await page.query_selector('.tab:has-text("扩展功能"), [data-tab="extension"]')
        if ext_tab:
            await ext_tab.click()
            await page.wait_for_timeout(2000)
        else:
            await page.goto(target_url)
            await page.wait_for_timeout(3000)

    # 再点击智能助手
    if '/extension/' in page.url and '/extension/trigger' not in page.url:
        nav_items = await page.query_selector_all('.nav-item, .menu-item, a')
        for item in nav_items:
            txt = (await item.text_content() or "").strip()
            if '智能助手' in txt:
                await item.click()
                await page.wait_for_timeout(2500)
                break

    print(f"  当前URL: {page.url}")
    return 'extension/trigger' in page.url or 'trigger' in page.url


async def process_form(page, form_entry, module_name, output_dir=None, screenshots_dir=None, docs_dir=None):
    """
    处理单个表单：导航 -> 筛选 -> 逐个采集 -> 返回（v8.0 新目录结构）。

    Args:
        form_entry: {name, type, formId, ...}
        module_name: 所属模块名
        output_dir: 输出根目录（兼容性保留）
        screenshots_dir: 截图目录
        docs_dir: 文档目录

    Returns:
        {formName, formId, assistants: [...], assistantCount}
    """
    form_name = form_entry.get('name', '未知表单')
    form_id = form_entry.get('formId', '')
    
    # v8.0: 使用传入的目录或从 MODULE_DIRS 获取
    if screenshots_dir is None or docs_dir is None:
        module_dirs = MODULE_DIRS.get(module_name, {})
        screenshots_dir = screenshots_dir or module_dirs.get('screenshots', output_dir or OUTPUT_DIR)
        docs_dir = docs_dir or module_dirs.get('docs', output_dir or OUTPUT_DIR)

    if not form_id:
        print(f"  ⏭️ 跳过 {form_name}: 无 formId")
        return {"formName": form_name, "formId": "", "assistantCount": 0, "assistants": [], "skipped": True}

    print(f"\n{'━'*60}")
    print(f"📋 表单: {form_name} | 类型: {form_entry.get('type', '?')} | 模块: {module_name}")
    print(f"  文档: {docs_dir}")
    print(f"  截图: {screenshots_dir}")
    print(f"{'━'*60}")

    # 导航到该表单
    nav_ok = await navigate_to_form(page, form_id, form_name)
    if not nav_ok:
        print(f"  ⚠️ 导航失败")
        await debug_dump(page, f"ERROR_nav_{form_name[:20]}", out_dir=screenshots_dir)
        return {"formName": form_name, "formId": form_id, "assistantCount": 0, "assistants": [], "error": "navigation_failed"}

    # v8.0: 只在调试模式下保存列表截图
    # await page.screenshot(path=f"{screenshots_dir}/00_list_{form_name[:10]}_{datetime.now().strftime('%H%M%S')}.png", full_page=True)

    # 筛选（每次表单切换后都强制重新筛选，防止上一个表单的筛选状态残留）
    try:
        count = await filter_trigger_only(page, output_dir=screenshots_dir, force_refilter=True)
    except Exception as e:
        print(f"  ❌ 筛选失败: {e}")
        return {"formName": form_name, "formId": form_id, "assistantCount": 0, "assistants": [], "error": str(e)}

    if count == 0:
        print(f"  ℹ️ 该表单无智能助手（作为触发动作）")
        # 记录零结果（放在文档目录）
        zero_result = {
            "formName": form_name,
            "formId": form_id,
            "moduleName": module_name,
            "formType": form_entry.get('type'),
            "assistantCount": 0,
            "assistants": [],
            "captured_at": datetime.now().isoformat()
        }
        safe_name = form_name.replace('/', '_').replace('\\', '_').strip()
        with open(f"{docs_dir}/{safe_name}_zero.json", 'w', encoding='utf-8') as f:
            json.dump(zero_result, f, ensure_ascii=False, indent=2)
        return zero_result

    # 获取助手列表
    assistant_list = await get_assistant_names(page)
    print(f"\n  发现 {len(assistant_list)} 个智能助手:")
    for i, ast in enumerate(assistant_list):
        name = ast['name']
        trigger = ast.get('triggerEvent', '')
        display = f"{name} ({trigger})" if trigger else name
        print(f"    {i+1}. {display[:70]}")

    all_results = []
    for idx, ast_info in enumerate(assistant_list):
        name = ast_info['name']
        trigger_event = ast_info.get('triggerEvent', '')
        try:
            print(f"\n  采集 [{idx+1}/{len(assistant_list)}]: {name[:50]}")

            clicked = await find_and_click_edit_button(page, idx)
            if not clicked:
                print("    ❌ 编辑按钮未找到")
                await debug_dump(page, f"ERROR_no_edit_{idx}", out_dir=screenshots_dir)
                continue

            await page.wait_for_timeout(3000)

            if '/automation/' not in page.url:
                print("    ⚠️ URL未变，重试一次...")
                await debug_dump(page, f"DEBUG_retry_nav_{idx}", out_dir=screenshots_dir)
                clicked2 = await find_and_click_edit_button(page, idx)
                if clicked2:
                    await page.wait_for_timeout(3000)
                if '/automation/' not in page.url:
                    print("    ❌ 仍无法进入画布")
                    continue

            # v8.0: 传递分离的文档和截图目录
            result = await extract_single_assistant(
                page, idx, name, 
                output_dir=None,
                screenshots_dir=screenshots_dir,
                docs_dir=docs_dir
            )
            # v8.0: 添加触发事件到结果
            result['triggerEvent'] = trigger_event
            all_results.append(result)

            await go_back_to_list(page)
            print(f"    ← 返回列表")

        except Exception as e:
            print(f"    ⚠️ 异常: {e}")
            # v8.0.6: 只在截图目录保存错误截图（受 ENABLE_SCREENSHOTS 控制）
            if ENABLE_SCREENSHOTS:
                await page.screenshot(path=f"{screenshots_dir}/error_{form_name[:10]}_{idx}.png")
            try:
                await go_back_to_list(page)
            except:
                pass

    # v8.0: 表单汇总放在文档目录
    form_summary = {
        "formName": form_name,
        "formId": form_id,
        "moduleName": module_name,
        "formType": form_entry.get('type'),
        "assistantCount": len(all_results),
        "assistants": all_results,
        "captured_at": datetime.now().isoformat()
    }
    
    safe_name = form_name.replace('/', '_').replace('\\', '_').strip()
    with open(f"{docs_dir}/{safe_name}_summary.json", 'w', encoding='utf-8') as f:
        json.dump(form_summary, f, ensure_ascii=False, indent=2)

    print(f"\n  ✅ 表单完成: {form_name} — {len(all_results)}个助手")
    return form_summary


# ============================================================
# Phase C: 模块汇总报告
# ============================================================

def generate_form_markdown_report(form_data, output_path):
    """生成单个表单的 Markdown 报告 (v8.0.9: 分支树形缩进)"""
    form_name = form_data.get('formName', NodeType.UNKNOWN)
    assistants = form_data.get('assistants', [])

    lines = [
        f"# {form_name}",
        "",
        f"表单ID: `{form_data.get('formId', '')}` | 模块: {form_data.get('moduleName', '')} | 助手数: {len(assistants)}",
        "",
    ]

    for r in assistants:
        lines.append(f"## {(r.get('index',0)+1)}. {r.get('name', NodeType.UNKNOWN)[:80]}")
        lines.append("")
        nodes = r.get('nodes', [])
        if nodes:
            lines.append("**流程**:")
            
            # v8.0.9: 跟踪当前分支，用于缩进
            current_branch = ""  # 当前所属分支
            node_counter = 0  # 全局节点编号
            
            for ni, n in enumerate(nodes):
                nt = n.get('type', NodeType.UNKNOWN)
                nn = n.get('name', '')[:60]
                config = n.get('config') or {}
                fields = config.get('fields', [])
                mappings = config.get('mappings', [])
                
                # v8.0.9: 分支节点 - 更新当前分支
                branch_indent = ""
                if nt == NodeType.BRANCH:
                    current_branch = nn
                    node_counter += 1
                    lines.append(f"{node_counter}. **[{nt}]** {nn}")
                elif nt == NodeType.FALLBACK:
                    node_counter += 1
                    lines.append(f"{node_counter}. **[{NodeType.FALLBACK}]** {nn}（所有条件都不满足时执行本分支）")
                    lines.append("")
                    continue
                else:
                    node_counter += 1
                    if current_branch:
                        branch_indent = "   "
                        lines.append(f"{node_counter}. {branch_indent}└─ **[{nt}]** {nn}")
                    else:
                        lines.append(f"{node_counter}. **[{nt}]** {nn}")
                
                # v8.0.7: 精简字段显示，只显示关键配置
                if fields:
                    for f in fields:
                        ft = f.get('title', '')
                        if '设置字段值' in ft: continue
                        fb = f.get('body','')
                        
                        # v8.0.9: 根据分支状态调整缩进
                        config_indent = "   " + branch_indent if branch_indent else "   "

                        # v8.0.9: 特殊处理查询条件和分支条件（已结构化）
                        # 查询条件用 ； 分隔，分支条件用 ｜ 分隔
                        if ('查询条件' in ft or '筛选' in ft or '删除条件' in ft or '条件设置' in ft) and ('；' in fb or ' ｜ ' in fb):
                            lines.append(f"{config_indent}- {ft}:")
                            # 使用正确的分隔符分割
                            if '；' in fb:
                                parts = fb.split('；')
                            else:
                                parts = fb.split(' ｜ ')
                            item_indent = "      " + branch_indent if branch_indent else "     "
                            for part in parts[:8]:
                                if '|||' in part:
                                    a_x_b = part.split('|||')
                                    if len(a_x_b) == 3:
                                        a = a_x_b[0].strip()
                                        x = a_x_b[1].strip()
                                        b = a_x_b[2].strip()
                                        # v8.0.9: 处理字段名中包含完整AXB的情况
                                        # 策略：先检查B是否在A中并从末尾开始匹配，去除B后再检查X
                                        if b and b in a:
                                            idx = a.rfind(b)
                                            if idx + len(b) == len(a):
                                                a = a[:idx].strip()
                                        if x and x in a:
                                            idx = a.rfind(x)
                                            if idx + len(x) == len(a):
                                                a = a[:idx].strip()
                                        lines.append(f"{item_indent}- {a} | {x} | {b}")
                                    else:
                                        lines.append(f"{item_indent}- {part}")
                                else:
                                    lines.append(f"{item_indent}- {part}")
                            if len(parts) > 8:
                                lines.append(f"{item_indent}- ...(共{len(parts)}个条件)")
                            continue

                        # 只保留关键配置
                        if any(k in ft for k in ['触发', '查询', '修改', '删除', '目标表单', '筛选', '条件', '公式', '分支', '排序', '结果条数']):
                            lines.append(f"{config_indent}- {ft}: {fb[:60]}")
                
                # [v8.0.9] 结构化字段映射（新增/修改节点）
                # v8.1.0: 根据 INCLUDE_EMPTY_FIELD_MAPPINGS 配置过滤空值字段
                field_mappings = config.get('fieldMappings', [])
                if field_mappings:
                    # 过滤空值字段
                    filtered_mappings = [fm for fm in field_mappings 
                                        if fm.get('sourceType') != 'empty' or INCLUDE_EMPTY_FIELD_MAPPINGS]
                    if filtered_mappings:
                        field_indent = "   " + branch_indent if branch_indent else "   "
                        lines.append(f"{field_indent}- 字段映射:")
                        fm_indent = "      " + branch_indent if branch_indent else "     "
                        for fm in filtered_mappings:
                            fname = fm.get('field', '')
                            stype = fm.get('sourceType', '?')
                            if stype == 'node':
                                if fm.get('sourceNode') and fm.get('sourceField'):
                                    sdetail = f"{fm.get('sourceNode','')} → {fm.get('sourceField','')}"
                                else:
                                    sdetail = fm.get('sourceRaw', '') or '节点字段'
                            elif stype == 'custom':
                                sdetail = f"`{fm.get('customValue','')}`"
                            elif stype == 'expression':
                                sdetail = f"`{fm.get('expression','')}`"
                            elif stype == 'empty':
                                sdetail = "(空值)"
                            else:
                                sdetail = fm.get('sourceRaw', '') or fm.get('note', '')
                            lines.append(f"{fm_indent}- {fname} = {sdetail}")
                
                # [v8.0.6] 仅当无结构化映射时显示原始映射
                if mappings and not field_mappings:
                    lines.append("   - 原始映射:")
                    for m in mappings[:5]:
                        lines.append(f"     - {m[:60]}")
                    if len(mappings) > 5:
                        lines.append(f"     - ...(共{len(mappings)}条)")
                
                # 分支执行规则
                if config and config.get('branchRuleActive'):
                    rule_indent = "   " + branch_indent if branch_indent else "   "
                    lines.append(f"{rule_indent}- 分支: {config['branchRuleActive']}")
            lines.append("")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return output_path


def generate_module_summary(module_name, form_summaries, output_dir, docs_dir=None):
    """生成模块级汇总报告
    
    Args:
        module_name: 模块名称
        form_summaries: 表单汇总列表
        output_dir: 报告输出目录（MD文件）
        docs_dir: 过程文件输出目录（JSON文件），如不提供则使用output_dir
    """
    if docs_dir is None:
        docs_dir = output_dir
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    total_assistants = sum(f.get('assistantCount', 0) for f in form_summaries)
    total_forms_with = sum(1 for f in form_summaries if f.get('assistantCount', 0) > 0)
    total_forms_zero = sum(1 for f in form_summaries if f.get('assistantCount', 0) == 0 and not f.get('skipped'))
    total_skipped = sum(1 for f in form_summaries if f.get('skipped'))

    lines = [
        f"# {module_name} - 智能助手模块汇总",
        "",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"## 统计概览",
        "",
        f"- 表单总数: **{len(form_summaries)}** 个",
        f"- 有智能助手的表单: **{total_forms_with}** 个",
        f"- 无智能助手的表单: **{total_forms_zero}** 个",
        f"- 跳过的非表单项: **{total_skipped}** 个",
        f"- **智能助手总数: {total_assistants}** 个",
        "",
        "---",
        "",
        f"## 各表单详情",
        "",
    ]

    for fs in form_summaries:
        fn = fs.get('formName', '?')
        ft = fs.get('formType', '?')
        ac = fs.get('assistantCount', 0)
        fid = fs.get('formId', '')

        status = "✅" if ac > 0 else ("ℹ️" if not fs.get('skipped') else "⏭️")
        lines.append(f"### {status} {fn} (`{ft}`)")
        lines.append(f"")

        if ac > 0:
            lines.append(f"| # | 智能助手名称 | 触发事件 | 业务类型 | 节点数 |")
            lines.append(f"|---|---|---|---|---|")
            for ai, ast in enumerate(fs.get('assistants', [])):
                name = ast.get('name', '')
                # v8.0: 使用独立的 triggerEvent 字段
                trigger_event = ast.get('triggerEvent', '')
                business_type = ast.get('businessType', '')

                node_count = len(ast.get('nodes', []))
                short_name = name[:45]
                short_trigger = trigger_event[:20] if trigger_event else '—'
                short_business = business_type[:15] if business_type else '—'
                lines.append(f"| {ai+1} | {short_name} | {short_trigger} | {short_business} | {node_count} |")
        else:
            lines.append(f"*无智能助手*" if not fs.get('skipped') else "*非表单类型，已跳过*")

        lines.append("")

    # 保存
    report_path = f"{output_dir}/{module_name}_summary.md"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    # 同时保存 JSON 汇总
    summary_json = {
        "moduleName": module_name,
        "generatedAt": datetime.now().isoformat(),
        "stats": {
            "totalForms": len(form_summaries),
            "formsWithAssistants": total_forms_with,
            "formsWithoutAssistants:": total_forms_zero,
            "skippedForms": total_skipped,
            "totalAssistants": total_assistants,
        },
        "forms": form_summaries,
    }
    json_path = f"{docs_dir}/{module_name}_summary.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary_json, f, ensure_ascii=False, indent=2)

    print(f"\n  📊 模块报告: {report_path}")
    print(f"  📊 模块JSON: {json_path}")

    return report_path, summary_json


# ============================================================
# 主流程
# ============================================================

async def run_module(page, module, output_root=None):
    """
    执行单个模块的完整遍历（v8.0 新目录结构）。
    
    v8.0 目录结构：
        {output_root}/{module_name}_{timestamp}/
            ├── 文档/          # JSON、Markdown 报告
            └── 截图/          # 截图文件

    Args:
        module: {name, type, children: [...]}
        output_root: 输出根目录

    Returns:
        (module_name, form_summaries_list)
    """
    global MODULE_NAME
    module_name = module.get('name', NodeType.UNKNOWN)
    MODULE_NAME = module_name

    d = output_root or OUTPUT_DIR
    
    # v8.0: 新目录结构 - 模块名_时间戳/文档/截图/报告
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    module_dir_name = f"{module_name}_{timestamp}"
    module_dir = f"{d}/{module_dir_name}"
    docs_dir = f"{module_dir}/过程文件"
    screenshots_dir = f"{module_dir}/截图"
    reports_dir = f"{module_dir}/报告"
    
    os.makedirs(docs_dir, exist_ok=True)
    os.makedirs(screenshots_dir, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)
    
    # 存储路径供其他函数使用
    MODULE_DIRS[module_name] = {
        'base': module_dir,
        'docs': docs_dir,
        'screenshots': screenshots_dir,
        'reports': reports_dir,
    }

    print(f"\n\n{'█'*60}")
    print(f"▶ 模块: {module_name}")
    print(f"  输出: {module_dir}")
    print(f"{'█'*60}")

    # ====== 关键：展开目标模块获取完整子表单列表 ======
    # 如果 Phase A 时该模块没有子项（文件夹未展开），需要：
    # 1. 回到应用主页
    # 2. 点击该模块的 L0 group 节点展开它
    # 3. 重新提取树节点，获取展开后的子表单
    
    if not module.get('children') or len(module.get('children', [])) == 0:
        print(f"  ⚠️ 模块 {module_name} 在初始提取时无子项，尝试展开...")
        
        # 回到主页（如果不在）
        await navigate_to_app_home(page, APP_ID)
        await page.wait_for_timeout(1500)
        
        # 找到并点击目标模块的 L0 节点
        expanded = await page.evaluate("""
            (targetName) => {
                const tree = document.querySelector('.fx-app-menu-tree.fx-indicator-tree');
                if (!tree) return {error: 'no tree'};
                
                const allNodes = tree.querySelectorAll(':scope > .tree-node');
                
                for (let i = 0; i < allNodes.length; i++) {
                    const n = allNodes[i];
                    const nameEl = n.querySelector('.name-text');
                    const name = nameEl ? nameEl.textContent?.trim() : '';
                    
                    // 检查是否是 L0 的 group 类型且名称匹配
                    if (name === targetName) {
                        const svgIcon = n.querySelector('svg.x-biz-entry-icon');
                        let isGroup = false;
                        if (svgIcon) {
                            isGroup = (svgIcon.getAttribute('class') || '').includes('group');
                        }
                        
                        if (isGroup) {
                            const contentWrapper = n.querySelector('.node-content-wrapper');
                            if (contentWrapper) {
                                contentWrapper.click();
                                return {clicked: true, index: i, name: name};
                            }
                        }
                    }
                }
                
                return {clicked: false, error: 'module node not found: ' + targetName};
            }
        """, module_name)
        
        print(f"  展开结果: {json.dumps(expanded, ensure_ascii=False)}")
        
        if expanded.get('clicked'):
            await page.wait_for_timeout(2000)
            
            # 检查是否还在主页（点击可能触发了页面跳转）
            still_home = await page.evaluate("""
                () => !!document.querySelector('.fx-app-menu-tree.fx-indicator-tree')
            """)
            
            if still_home:
                # Step 1: 先获取目标模块下的子项（可能包含二级目录）
                first_raw = await extract_all_tree_nodes(page)
                first_items = first_raw.get('items', [])
                first_modules = build_module_structure(first_items)
                
                # 更新当前模块的 children
                for m in first_modules:
                    if m['name'] == module_name and m.get('children'):
                        module['children'] = m['children']
                        print(f"  ✓ 一级展开后发现 {len(m['children'])} 个子项")
                        break
                
                # Step 2: 找出目标模块下的二级目录(group类型)并逐个展开
                sub_folders = [c for c in module.get('children', []) if c.get('type') == 'group']
                
                if sub_folders:
                    print(f"  📂 发现 {len(sub_folders)} 个二级目录，逐个展开...")
                    
                    for folder in sub_folders:
                        folder_name = folder.get('name', '')
                        print(f"     → 展开: {folder_name}")
                        
                        # 只展开目标模块下的这个特定二级目录
                        expand_result = await page.evaluate("""
                            (targetName) => {
                                const tree = document.querySelector('.fx-app-menu-tree.fx-indicator-tree');
                                if (!tree) return {error: 'no tree'};
                                
                                // 递归查找目标名称的 group 节点
                                function findGroupNode(element, name) {
                                    const nodes = element.querySelectorAll(':scope > .tree-node');
                                    for (let i = 0; i < nodes.length; i++) {
                                        const n = nodes[i];
                                        const nameEl = n.querySelector('.name-text');
                                        const nameText = nameEl ? nameEl.textContent?.trim() : '';
                                        
                                        const svgIcon = n.querySelector('svg.x-biz-entry-icon');
                                        let isGroup = false;
                                        if (svgIcon) {
                                            isGroup = (svgIcon.getAttribute('class') || '').includes('group');
                                        }
                                        
                                        // 名称匹配且是group类型
                                        if (nameText === name && isGroup) {
                                            // 检查是否已展开
                                            const hasChildren = n.querySelector('.tree-children') !== null;
                                            if (!hasChildren) {
                                                const contentWrapper = n.querySelector('.node-content-wrapper');
                                                if (contentWrapper) {
                                                    contentWrapper.click();
                                                    return {expanded: true, name: name};
                                                }
                                            }
                                            return {alreadyExpanded: true, name: name};
                                        }
                                        
                                        // 递归查找子节点
                                        const childContainer = n.querySelector('.tree-children');
                                        if (childContainer) {
                                            const result = findGroupNode(childContainer, name);
                                            if (result && (result.expanded || result.alreadyExpanded)) {
                                                return result;
                                            }
                                        }
                                    }
                                    return null;
                                }
                                
                                return findGroupNode(tree, targetName);
                            }
                        """, folder_name)
                        
                        if expand_result and not expand_result.get('error'):
                            await page.wait_for_timeout(800)
                            status = "已展开" if expand_result.get('expanded') else "已存在"
                            print(f"       ✓ {status}: {folder_name}")
                        else:
                            print(f"       ⚠️ 未找到或失败: {folder_name}")
                    
                    # 等待所有子目录加载完成
                    await page.wait_for_timeout(1000)
                
                # Step 3: 最终获取所有节点（包含二级目录下的表单）
                final_raw = await extract_all_tree_nodes(page)
                final_items = final_raw.get('items', [])
                final_modules = build_module_structure(final_items)
                
                # 更新最终结果
                for m in final_modules:
                    if m['name'] == module_name and m.get('children'):
                        module['children'] = m['children']
                        print(f"\n  ✓ 最终共发现 {len(m['children'])} 个子项")
                        break
            else:
                print("  ⚠️ 展开后离开主页，尝试返回...")
                await navigate_to_app_home(page, APP_ID)
                await page.wait_for_timeout(2000)
    
    # 过滤有效的表单（排除文件夹和仪表盘）
    valid_forms = [c for c in module.get('children', []) if c.get('type') not in SKIP_ICON_TYPES]
    skipped = [c for c in module.get('children', []) if c.get('type') in SKIP_ICON_TYPES]

    # v8.0.9: 根据表单名称关键字排除
    keyword_skipped = []
    forms_to_process = []
    for vf in valid_forms:
        form_name = vf.get('name', '')
        if should_skip_form(form_name):
            keyword_skipped.append((vf, _skip_keyword(form_name)))
        else:
            forms_to_process.append(vf)

    # 分类统计
    folders = [c for c in skipped if c.get('type') == 'group']
    dashboards = [c for c in skipped if c.get('type') == 'dash']

    if folders:
        print(f"\n  📂 发现 {len(folders)} 个子目录（已展开，其表单已包含在下方列表中）:")
        for f in folders:
            print(f"     📁 {f.get('name')}")

    if dashboards:
        print(f"\n  跳过 {len(dashboards)} 个仪表盘:")
        for d in dashboards:
            print(f"     ⏭️ [{d.get('type')}] {d.get('name')}")

    if keyword_skipped:
        print(f"\n  ⏭️ 跳过 {len(keyword_skipped)} 个名称含关键字的表单:")
        for vf, keyword in keyword_skipped:
            print(f"     ⏭️ [{keyword}] {vf.get('name')}")

    if not forms_to_process:
        print(f"\n  ⚠️ 模块 {module_name} 没有有效表单")
        return module_name, []

    print(f"\n  将处理 {len(forms_to_process)} 个表单:")
    for vf in forms_to_process:
        print(f"    📄 [{vf.get('type')}] {vf.get('name')}")

    # v8.0: 获取当前模块的目录配置
    module_dirs = MODULE_DIRS.get(module_name, {})
    docs_dir = module_dirs.get('docs', module_dir)
    screenshots_dir = module_dirs.get('screenshots', module_dir)
    reports_dir = module_dirs.get('reports', module_dir)
    
    # 逐表单处理
    form_summaries = []
    for fi, form_entry in enumerate(forms_to_process):
        print(f"\n\n{'▔'*40} [{fi+1}/{len(forms_to_process)}] {'▔'*40}")

        try:
            # v8.0: 不再传递 output_dir，process_form 会从 MODULE_DIRS 获取
            summary = await process_form(page, form_entry, module_name)
            form_summaries.append(summary)
        except Exception as e:
            print(f"  ❌ 表单处理异常: {e}")
            form_summaries.append({
                "formName": form_entry.get('name', ''),
                "formId": form_entry.get('formId', ''),
                "assistantCount": 0,
                "assistants": [],
                "error": str(e)
            })

    # 生成模块汇总（v8.1.0: 放在报告目录）
    if form_summaries:
        md_path, json_data = generate_module_summary(module_name, form_summaries, output_dir=reports_dir, docs_dir=docs_dir)

        # v8.1.0: 为每个有助手的表单单独生成MD报告（放在报告目录）
        for fs in form_summaries:
            if fs.get('assistantCount', 0) > 0:
                safe_name = fs.get('formName', '').replace('/', '_').replace('\\', '_').strip()
                md_out = f"{reports_dir}/{safe_name}_report.md"
                generate_form_markdown_report(fs, md_out)

    return module_name, form_summaries


async def main():
    global OUTPUT_DIR, APP_ID

    print("\n" + "=" * 70)
    print("简道云智能助手批量截取 v8.0 - 数据质量优化版")
    print("=" * 70)
    print("改进: 文本清洗 | 业务分类 | 智能截图 | 新目录结构")
    print("=" * 70)

    # ---- 参数处理 ----
    args = sys.argv[1:]
    user_module_input = None
    list_only = False

    for arg in args:
        if arg == '--list-modules':
            list_only = True
        elif arg.startswith('--app-id='):
            APP_ID = arg.split('=', 1)[1]
        elif arg.startswith('--output='):
            OUTPUT_DIR = arg.split('=', 1)[1]
        elif not arg.startswith('--'):
            user_module_input = arg

    if not APP_ID:
        # 默认测试应用
        APP_ID = "69db868cc68a628a7d0f207f"

    print(f"  应用ID: {APP_ID}")
    print(f"  输出目录: {OUTPUT_DIR}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    _, _, page = await connect()
    print("✓ Chromium 连接成功")

    # ======== Phase A: 提取导航树 ========
    module_tree = await build_module_tree(page, APP_ID)

    if not module_tree:
        print("\n❌ 无法提取模块树，终止")
        return

    # ======== 选择模块 ========
    selected_modules = select_target_modules(module_tree, user_input=user_module_input)

    if list_only:
        print("\n\n===== 模块列表演示完毕 =====")
        return

    if not selected_modules:
        print("\n❌ 未选择任何模块")
        return

    # ======== Phase B: 按模块遍历 ========
    all_module_results = {}

    print(f"\n\n{'═'*70}")
    print(f"Phase B: 开始遍历 {len(selected_modules)} 个模块")
    print(f"{'═'*70}")

    for mi, module in enumerate(selected_modules):
        print(f"\n\n{'╔'*60}")
        print(f"║ 进度: 模块 {mi+1}/{len(selected_modules)} — {module['name']}")
        print(f"{'╚'*60}")

        try:
            mod_name, summaries = await run_module(page, module, output_root=OUTPUT_DIR)
            all_module_results[mod_name] = summaries
        except Exception as e:
            print(f"\n  ❌ 模块处理严重错误: {e}")
            import traceback
            traceback.print_exc()

    # ======== Phase C: 全局汇总 ========
    print(f"\n\n{'═'*70}")
    print("Phase C: 生成全局汇总")
    print(f"{'═'*70}")

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    # 统计总览
    total_modules = len(all_module_results)
    total_forms = sum(len(v) for v in all_module_results.values())
    total_assistants = sum(
        sum(f.get('assistantCount', 0) for f in forms)
        for forms in all_module_results.values()
    )

    final_lines = [
        f"# ERP系统 - 智能助手全局汇总",
        "",
        f"**应用ID**: {APP_ID}",
        f"**采集时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**版本**: v8.0",
        "",
        f"## 总体统计",
        "",
        f"| 指标 | 数量 |",
        f"|------|------|",
        f"| 已遍历模块 | {total_modules} |",
        f"| 已处理表单 | {total_forms} |",
        f"| **智能助手总数** | **{total_assistants}** |",
        "",
        "---",
        "",
        f"## 模块明细",
        "",
    ]

    for mod_name, forms in all_module_results.items():
        mod_total = sum(f.get('assistantCount', 0) for f in forms)
        mod_forms_with = sum(1 for f in forms if f.get('assistantCount', 0) > 0)
        final_lines.append(f"### {mod_name} ({mod_forms_with}/{len(forms)} 有助手, 共{mod_total}个)")

        for f in forms:
            fn = f.get('formName', '?')
            ac = f.get('assistantCount', 0)
            mark = "✅" if ac > 0 else ("ℹ️" if not f.get('skipped') else "⏭️")
            final_lines.append(f"- {mark} {fn}: {ac}个助手")

            # 列出每个助手
            for ast in f.get('assistants', []):
                aname = ast.get('name', '')[:60]
                anodes = len(ast.get('nodes', []))
                final_lines.append(f"  - {aname} ({anodes}节点)")

        final_lines.append("")

    final_lines.extend([
        "---",
        "",
        "## 后续分析方向",
        "",
        "1. **跨模块调用关系矩阵** — 分析哪些表单的智能助手会写入其他模块的表单",
        "2. **触发事件覆盖度** — 检查关键业务事件（新增/修改/删除/流程流转）是否有对应自动化",
        "3. **数据流图绘制** — 基于节点配置还原数据流向",
        "",
        f"*由 v8.0 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
    ])

    # v8.1.0: 全局汇总 - MD放报告目录，JSON放过程文件目录
    if all_module_results:
        first_module = list(all_module_results.keys())[0]
        global_reports_dir = MODULE_DIRS.get(first_module, {}).get('reports', OUTPUT_DIR)
        global_docs_dir = MODULE_DIRS.get(first_module, {}).get('docs', OUTPUT_DIR)
        
        global_md = f"{global_reports_dir}/GLOBAL_SUMMARY_{ts}.md"
        with open(global_md, 'w', encoding='utf-8') as f:
            f.write('\n'.join(final_lines))
        print(f"  📋 全局报告: {global_md}")

        global_json = {
            "appId": APP_ID,
            "version": "8.0",
            "generatedAt": datetime.now().isoformat(),
            "stats": {
                "modules": total_modules,
                "forms": total_forms,
                "totalAssistants": total_assistants,
            },
            "modules": all_module_results,
        }
        global_json_path = f"{global_docs_dir}/GLOBAL_SUMMARY_{ts}.json"
        with open(global_json_path, 'w', encoding='utf-8') as f:
            json.dump(global_json, f, ensure_ascii=False, indent=2)
        print(f"  📋 全局JSON: {global_json_path}")
    else:
        print("  ⚠️ 无模块结果，跳过全局汇总")

    # 最终总结
    print(f"\n\n{'='*70}")
    print(f"✅ v8.0 全部完成!")
    print(f"   模块: {total_modules}")
    print(f"   表单: {total_forms}")
    print(f"   智能助手: {total_assistants}")
    print(f"   输出: {OUTPUT_DIR}")
    print(f"   改进: 文本清洗 | 业务分类 | 智能截图")
    print(f"{'='*70}")


if __name__ == '__main__':
    asyncio.run(main())
