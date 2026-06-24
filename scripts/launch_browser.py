#!/usr/bin/env python3
"""
简道云智能助手读取 - 启动器
自动检测已安装的浏览器（Edge / Chrome / Chromium），通过 CDP 启动。

支持浏览器（按优先级）：
  1. Microsoft Edge
  2. Google Chrome
  3. Playwright Chromium（需先运行 playwright install chromium）

支持系统：Windows / macOS / Linux

工作流程：
1. 自动检测并启动浏览器（headed 模式，CDP 端口 9222）
2. 用户在浏览器中登录简道云（首次）
3. 浏览器窗口保持打开，用户导航到目标应用
4. 运行 capture_all_assistants.py 或 gui_capture.py 进行采集
"""

import asyncio
import os
import sys
import subprocess
from pathlib import Path

# ── 常量 ──────────────────────────────────────────────────────

CDP_PORT = 9222
CDP_URL = f"http://localhost:{CDP_PORT}"

# 持久化 profile 目录（登录一次后记住状态）
PROFILE_DIR = os.path.expanduser("~/.workbuddy/jdy-browser-profile")

# 常见浏览器可执行文件路径（按优先级）
_WINDOWS_PATHS = {
    'edge': [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ],
    'chrome': [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ],
}

_DARWIN_PATHS = {
    'edge': [
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
    'chrome': [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ],
}

_LINUX_PATHS = {
    'edge': [
        "/usr/bin/microsoft-edge",
        "/usr/bin/microsoft-edge-stable",
    ],
    'chrome': [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ],
}

_ALL_PATHS = {
    'win32': _WINDOWS_PATHS,
    'darwin': _DARWIN_PATHS,
    'linux': _LINUX_PATHS,
}


# ── 浏览器检测 ────────────────────────────────────────────────

def find_browser():
    """自动检测已安装的浏览器，返回 (name, executable_path) 或 None"""
    platform_paths = _ALL_PATHS.get(sys.platform, _LINUX_PATHS)

    for browser_name in ('edge', 'chrome'):
        for path in platform_paths.get(browser_name, []):
            if os.path.exists(path):
                return browser_name, path

    # Playwright Chromium 回退
    try:
        result = subprocess.run(
            [sys.executable, "-c", "from playwright._impl._driver import compute_driver_executable; print(compute_driver_executable())"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            driver_dir = Path(result.stdout.strip()).parent
            # 在 driver 相关目录中查找 chromium
            cache_dir = Path.home() / ".cache" / "ms-playwright"
            if sys.platform == 'darwin':
                cache_dir = Path.home() / "Library" / "Caches" / "ms-playwright"
            elif sys.platform == 'win32':
                cache_dir = Path(os.environ.get('LOCALAPPDATA', '')) / "ms-playwright"

            if cache_dir.exists():
                for d in sorted(cache_dir.iterdir(), reverse=True):
                    if 'chromium' in d.name.lower():
                        if sys.platform == 'darwin':
                            exe = d / "chrome-mac-arm64" / "Google Chrome for Testing.app" / "Contents" / "MacOS" / "Google Chrome for Testing"
                            if not exe.exists():
                                exe = d / "chrome-mac" / "Google Chrome for Testing.app" / "Contents" / "MacOS" / "Google Chrome for Testing"
                        elif sys.platform == 'win32':
                            exe = d / "chrome-win64" / "chrome.exe"
                            if not exe.exists():
                                exe = d / "chrome-win32" / "chrome.exe"
                        else:
                            exe = d / "chrome-linux" / "chrome"
                        if exe.exists():
                            return 'playwright-chromium', str(exe)
    except Exception:
        pass

    return None


def _get_browser_label(name):
    """返回浏览器的显示名称"""
    labels = {
        'edge': 'Microsoft Edge',
        'chrome': 'Google Chrome',
        'playwright-chromium': 'Playwright Chromium',
    }
    return labels.get(name, name)


# ── CDP 端口检测 ──────────────────────────────────────────────

def check_cdp_running():
    """检查 CDP 端口是否已有浏览器在监听"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        sock.connect(('127.0.0.1', CDP_PORT))
        sock.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


# ── 启动 ──────────────────────────────────────────────────────

def launch_browser(exe_path, profile_dir):
    """以 CDP 模式启动浏览器，返回 subprocess.Popen"""
    args = [
        exe_path,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--disable-blink-features=AutomationControlled",
        "--disable-extensions",
        "https://www.jiandaoyun.com",
    ]
    # 清理锁文件
    lock = os.path.join(profile_dir, "SingletonLock")
    if os.path.exists(lock):
        try:
            os.remove(lock)
        except OSError:
            pass

    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def main():
    print("=" * 60)
    print("简道云智能助手读取器 - 启动器")
    print("=" * 60)

    # 检查是否已有浏览器在 CDP 端口上运行
    if check_cdp_running():
        print(f"✅ 检测到已有浏览器在 localhost:{CDP_PORT} 运行")
        print("   无需重复启动，可直接运行采集脚本。")
        return

    # 自动检测浏览器
    result = find_browser()
    if not result:
        print("❌ 未找到可用的浏览器！")
        print("   请安装以下任一浏览器：")
        print("   - Microsoft Edge  https://www.microsoft.com/edge")
        print("   - Google Chrome   https://www.google.com/chrome")
        print("   或运行: python -m playwright install chromium")
        return

    browser_name, exe_path = result
    label = _get_browser_label(browser_name)

    print(f"✅ 检测到浏览器: {label}")
    print(f"   路径: {exe_path}")
    print(f"📁 持久化 Profile: {PROFILE_DIR}")

    os.makedirs(PROFILE_DIR, exist_ok=True)

    # 启动浏览器
    print(f"\n🚀 启动 {label}...")
    proc = launch_browser(exe_path, PROFILE_DIR)

    # 等待 CDP 端口就绪
    print("   等待浏览器就绪...")
    for i in range(30):
        if check_cdp_running():
            break
        await asyncio.sleep(1)
    else:
        print("❌ 浏览器启动超时，请检查是否有其他实例占用了端口 9222")
        proc.terminate()
        return

    print(f"\n{'='*60}")
    print(f"✅ {label} 已启动！CDP 端口: {CDP_PORT}")
    print(f"{'='*60}")
    print("""
下一步：
1. 在浏览器中登录简道云（只需一次）
2. 导航到目标应用
3. 运行 GUI:  python scripts/gui_capture.py
   或 CLI:   python scripts/capture_all_assistants.py

按 Ctrl+C 退出
""")

    # 保持运行直到用户中断或浏览器关闭
    try:
        while proc.poll() is None:
            await asyncio.sleep(1)
        print("\n浏览器已关闭。")
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n正在关闭浏览器...")
        proc.terminate()


if __name__ == '__main__':
    asyncio.run(main())
