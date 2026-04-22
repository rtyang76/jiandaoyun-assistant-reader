#!/usr/bin/env python3
"""
简道云智能助手读取 - 启动器
使用用户已安装的 Playwright Chromium，建立持久化 session

工作流程：
1. 启动 Chromium（ headed 模式）
2. 用户在浏览器中登录简道云
3. 浏览器窗口保持打开
4. 用户手动导航到目标表单的智能助手面板
5. 之后调用 capture_jdy_assistant.py 进行自动化截取
"""

import asyncio
import sys
import os
import time
from pathlib import Path

# Chromium 可执行文件路径（Playwright 安装的版本）
CHROMIUM_APP = "/Users/yrt/Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/Google Chrome for Testing.app"
CHROMIUM_EXECUTABLE = f"{CHROMIUM_APP}/Contents/MacOS/Google Chrome for Testing"

# 持久化 profile 目录（登录一次后记住状态）
PROFILE_DIR = os.path.expanduser("~/.workbuddy/jdy-chrome-profile")

async def main():
    print("=" * 60)
    print("简道云智能助手读取器 - 启动器")
    print("=" * 60)

    # 检查 Chromium 是否存在
    if not os.path.exists(CHROMIUM_EXECUTABLE):
        print(f"❌ 错误：未找到 Chromium")
        print(f"   路径：{CHROMIUM_EXECUTABLE}")
        print(f"   请先运行：python3 -m playwright install chromium")
        return

    print(f"✅ 找到 Chromium: {CHROMIUM_EXECUTABLE}")
    print(f"📁 持久化 Profile: {PROFILE_DIR}")

    # 创建 profile 目录
    os.makedirs(PROFILE_DIR, exist_ok=True)

    from playwright.async_api import async_playwright

    print("\n🚀 启动 Chromium 浏览器...")
    print("   （首次运行需要登录简道云，之后会记住状态）")

    playwright = await async_playwright().start()

    try:
        context = await playwright.chromium.launch_persistent_context(
            executable_path=CHROMIUM_EXECUTABLE,
            headless=False,  # 显示浏览器窗口
            user_data_dir=PROFILE_DIR,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-first-run',
                '--disable-extensions',
            ],
            viewport={"width": 1440, "height": 900},
            timeout=30000,
        )

        page = context.pages[0] if context.pages else await context.new_page()

        # 导航到简道云
        print("\n🌐 打开简道云...")
        await page.goto("https://www.jiandaoyun.com", timeout=30000)
        await page.wait_for_timeout(2000)

        print(f"   当前页面: {page.url}")

        print("\n" + "=" * 60)
        print("✅ 浏览器已启动！")
        print("=" * 60)
        print("""
下一步：
1. 在浏览器中登录简道云（只需一次）
2. 导航到：目标应用 → 销售订单 → 表单设计 → 扩展功能 → 智能助手
3. 确认智能助手列表已显示
4. 然后告诉我"准备好了"，我会开始自动化截取

按 Ctrl+C 退出浏览器
""")

        # 保持浏览器打开
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\n关闭浏览器...")

    except Exception as e:
        print(f"\n❌ 启动失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await playwright.stop()

if __name__ == '__main__':
    asyncio.run(main())
