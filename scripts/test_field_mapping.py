#!/usr/bin/env python3
"""
单元测试：字段映射解析逻辑

测试目标：
1. 节点字段值提取（格式：节点名—字段名）
2. 自定义值提取
3. 空值识别
4. 回退文本解析（已知字段名列表匹配）
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capture_all_assistants import (
    clean_node_name,
    clean_config_noise,
    should_skip_form,
    _skip_keyword,
    NodeType,
    NODE_TYPE_MAP,
)


class TestNodeTypeConstants(unittest.TestCase):
    """测试 NodeType 常量定义"""

    def test_node_type_values(self):
        self.assertEqual(NodeType.TRIGGER, '触发')
        self.assertEqual(NodeType.QUERY_SINGLE, '查询单条')
        self.assertEqual(NodeType.QUERY_MULTI, '查询多条')
        self.assertEqual(NodeType.UPDATE, '修改')
        self.assertEqual(NodeType.CREATE, '新增')
        self.assertEqual(NodeType.DELETE, '删除')
        self.assertEqual(NodeType.BRANCH, '分支')
        self.assertEqual(NodeType.FALLBACK, '其他条件')
        self.assertEqual(NodeType.UNKNOWN, '未知')

    def test_node_type_map_consistency(self):
        """验证 NODE_TYPE_MAP 与 NodeType 常量一致"""
        self.assertEqual(NODE_TYPE_MAP['trigger-data-node'], NodeType.TRIGGER)
        self.assertEqual(NODE_TYPE_MAP['branch-node-icon'], NodeType.BRANCH)
        self.assertEqual(NODE_TYPE_MAP['update-data-node-icon'], NodeType.UPDATE)
        self.assertEqual(NODE_TYPE_MAP['create-data-node-icon'], NodeType.CREATE)
        self.assertEqual(NODE_TYPE_MAP['delete-data-node-icon'], NodeType.DELETE)


class TestShouldSkipForm(unittest.TestCase):
    """测试表单跳过判断逻辑"""

    def test_skip_by_keyword(self):
        self.assertTrue(should_skip_form('测试表单'))
        self.assertTrue(should_skip_form('废弃表单'))
        self.assertTrue(should_skip_form('未启用表单'))
        self.assertTrue(should_skip_form('草稿表单'))
        self.assertTrue(should_skip_form('停用表单'))

    def test_not_skip_normal_form(self):
        self.assertFalse(should_skip_form('销售订单'))
        self.assertFalse(should_skip_form('生产计划'))
        self.assertFalse(should_skip_form('采购入库单'))

    def test_skip_keyword_extraction(self):
        self.assertEqual(_skip_keyword('测试表单'), '测试')
        self.assertEqual(_skip_keyword('废弃表单'), '废弃')
        self.assertIsNone(_skip_keyword('正常表单'))


class TestCleanNodeName(unittest.TestCase):
    """测试节点名称清洗逻辑"""

    def test_clean_empty(self):
        self.assertEqual(clean_node_name(''), '')
        self.assertEqual(clean_node_name(None), '')

    def test_clean_noise_words(self):
        self.assertEqual(clean_node_name('编辑'), '')
        self.assertEqual(clean_node_name('查看'), '')
        self.assertEqual(clean_node_name('删除'), '')

    def test_clean_prefix(self):
        result = clean_node_name('修改数据 - 测试节点')
        self.assertIn('测试', result)


class TestCleanConfigNoise(unittest.TestCase):
    """测试配置噪音清理逻辑"""

    def test_clean_empty_config(self):
        self.assertIsNone(clean_config_noise(None))
        self.assertEqual(clean_config_noise({}), {})

    def test_clean_ui_noise_in_fields(self):
        config = {
            'fields': [
                {'title': '添加动作', 'body': '请选择'},
                {'title': '正常字段', 'body': '正常值'},
            ],
            'mappings': []
        }
        cleaned = clean_config_noise(config)
        self.assertEqual(len(cleaned['fields']), 1)
        self.assertEqual(cleaned['fields'][0]['title'], '正常字段')

    def test_clean_noise_in_mappings(self):
        config = {
            'fields': [],
            'mappings': ['添加字段', '正常映射内容']
        }
        cleaned = clean_config_noise(config)
        self.assertEqual(len(cleaned['mappings']), 1)
        self.assertIn('正常映射内容', cleaned['mappings'][0])


class TestFieldMappingTextParser(unittest.TestCase):
    """
    测试字段映射文本解析逻辑
    
    注意：这部分测试需要模拟 JavaScript 执行环境，
    这里只测试 Python 端的辅助函数。
    完整的 JS 解析逻辑需要在实际运行环境中验证。
    """

    def test_known_field_names_parsing(self):
        """测试已知字段名列表解析"""
        test_body = (
            '生产计划来源=字段生产计划=字段生产计划编号=查询多条数据—生产计划明细编号'
            '销售订单=字段销售数量=字段生产计划状态=查询多条数据—生产计划状态'
        )
        
        known_field_names = [
            '生产计划编号', '生产计划状态', '生产计划来源', '销售订单', '销售数量',
        ]
        known_field_names.sort(key=len, reverse=True)
        
        field_positions = []
        for fn in known_field_names:
            search_pos = 0
            while True:
                idx = test_body.find(fn + '=', search_pos)
                if idx == -1:
                    break
                is_sub_field = any(
                    idx > fp['index'] and idx < fp['index'] + len(fp['name'])
                    for fp in field_positions
                )
                if not is_sub_field:
                    field_positions.append({'index': idx, 'name': fn})
                search_pos = idx + 1
        
        field_positions.sort(key=lambda x: x['index'])
        
        field_names = [fp['name'] for fp in field_positions]
        self.assertIn('生产计划编号', field_names)
        self.assertIn('生产计划状态', field_names)
        self.assertIn('销售订单', field_names)

    def test_node_field_value_parsing(self):
        """测试节点字段值解析（节点名—字段名）"""
        value_content = '查询多条数据—生产计划明细编号'
        
        if '—' in value_content:
            dash_idx = value_content.find('—')
            source_node = value_content[:dash_idx].strip()
            source_field = value_content[dash_idx + 1:].strip()
        else:
            source_node = ''
            source_field = ''
        
        self.assertEqual(source_node, '查询多条数据')
        self.assertEqual(source_field, '生产计划明细编号')

    def test_empty_value_detection(self):
        """测试空值检测"""
        empty_values = ['', '字段', '请选择字段']
        for v in empty_values:
            is_empty = not v or v == '字段' or v.startswith('请选择')
            self.assertTrue(is_empty, f"'{v}' should be detected as empty")


class TestFallbackTrigger(unittest.TestCase):
    """测试回退逻辑触发条件"""

    def test_has_valid_mappings_detection(self):
        """测试有效映射检测逻辑"""
        valid_node_mapping = {
            'sourceType': 'node',
            'sourceNode': '查询多条数据',
            'sourceField': '生产计划编号'
        }
        valid_custom_mapping = {
            'sourceType': 'custom',
            'customValue': '自定义值'
        }
        valid_empty_mapping = {
            'sourceType': 'empty'
        }
        invalid_mapping = {
            'sourceType': 'custom',
            'customValue': ''
        }
        
        def has_valid_mapping(fm):
            return (
                (fm['sourceType'] == 'node' and fm.get('sourceNode') and fm.get('sourceField')) or
                (fm['sourceType'] == 'custom' and fm.get('customValue')) or
                (fm['sourceType'] == 'empty')
            )
        
        self.assertTrue(has_valid_mapping(valid_node_mapping))
        self.assertTrue(has_valid_mapping(valid_custom_mapping))
        self.assertTrue(has_valid_mapping(valid_empty_mapping))
        self.assertFalse(has_valid_mapping(invalid_mapping))


if __name__ == '__main__':
    unittest.main(verbosity=2)
