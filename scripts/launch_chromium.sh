#!/bin/bash
# 简道云智能助手读取器 - 启动 Chromium 并保持运行
# 使用方式: ./launch_chromium.sh

CHROMIUM="/Users/yrt/Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
PROFILE="$HOME/.workbuddy/jdy-chrome-profile"

# 清理锁文件
rm -f "$PROFILE/SingletonLock" 2>/dev/null

echo "启动 Chromium..."
"$CHROMIUM" \
  --user-data-dir="$PROFILE" \
  --remote-debugging-port=9222 \
  --no-first-run \
  --disable-blink-features=AutomationControlled \
  --disable-extensions \
  --no-sandbox \
  --disable-dev-shm-usage \
  "https://www.jiandaoyun.com"

echo "Chromium 已退出"
