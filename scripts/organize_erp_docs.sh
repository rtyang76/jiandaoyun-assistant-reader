#!/bin/bash
# ERP文档整理脚本
# 在 /Users/yrt/Developer/Work/erp-data-analysis 目录下执行

cd "/Users/yrt/Developer/Work/erp-data-analysis"

echo "🚀 开始整理ERP文档..."

# 1. 删除无效文件
echo "📁 删除系统文件和临时文件..."
rm -f .DS_Store
rm -f "聊天输入输出汇总.txt"

# 2. 创建目录结构
echo "📁 创建新目录..."
mkdir -p "06_工具脚本"
mkdir -p "99_归档资料/01_过程分析文件"
mkdir -p "99_归档资料/02_周报月报"
mkdir -p "99_归档资料/03_方案草稿"

# 3. 移动文件
echo "📁 移动文件..."
mv analyze_erp_excel.py "06_工具脚本/"
mv erp_structure_data.json "原始数据/"
mv "实施文档优化方案.md" "99_归档资料/03_方案草稿/"

# 4. 归档过程文件
echo "📁 归档过程文件..."
mv "模块分析过程文件"/* "99_归档资料/01_过程分析文件/" 2>/dev/null || true
rmdir "模块分析过程文件" 2>/dev/null || true

# 5. 归档周报月报
echo "📁 归档周报月报..."
mv "原始数据/周报月报年报纯文本"/* "99_归档资料/02_周报月报/" 2>/dev/null || true
rmdir "原始数据/周报月报年报纯文本" 2>/dev/null || true

# 6. 重命名主要目录（加序号）
echo "📁 重命名目录..."
mv "ERP实施文档" "01_ERP实施文档" 2>/dev/null || true
mv "实施文档优化版" "02_实施文档优化版" 2>/dev/null || true
mv "模块API验证最新版" "03_模块API验证" 2>/dev/null || true
mv "原始数据" "04_原始数据" 2>/dev/null || true
mv "智能助手采集数据" "05_智能助手采集数据" 2>/dev/null || true

echo "✅ 整理完成！"
echo ""
echo "📋 新的目录结构："
ls -la
