#!/usr/bin/env python3
"""
简道云智能助手采集工具 - GUI 界面
右键运行此脚本即可启动图形界面，无需命令行操作。

依赖：Python 内置 tkinter + playwright
"""
import asyncio
import json
import os
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk, scrolledtext, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture_all_assistants as core


# ── 工具 ──────────────────────────────────────────────────────

class TextRedirector:
    """将 print 输出重定向到 tkinter Text 控件"""
    def __init__(self, widget):
        self._w = widget

    def write(self, s):
        self._w.after(0, self._do, s)

    def _do(self, s):
        self._w.configure(state="normal")
        self._w.insert(tk.END, s)
        self._w.see(tk.END)
        self._w.configure(state="disabled")

    def flush(self):
        pass


def _count_valid(mod):
    """统计模块中有效表单数量"""
    return sum(
        1 for c in mod.get('children', [])
        if c.get('type') not in core.SKIP_ICON_TYPES
        and not core.should_skip_form(c.get('name', ''))
    )


# ── GUI ───────────────────────────────────────────────────────

class CaptureGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("简道云智能助手采集工具 v8.1")
        self.root.geometry("900x700")
        self.root.minsize(700, 500)

        self.module_tree = []
        self._running = False

        self._build_ui()
        sys.stdout = TextRedirector(self.log)
        sys.stderr = TextRedirector(self.log)

    # ── UI 构建 ────────────────────────────────────────────────

    def _build_ui(self):
        self._build_config()
        self._build_buttons()
        self._build_panels()

    def _build_config(self):
        frm = ttk.LabelFrame(self.root, text="配置", padding=10)
        frm.pack(fill=tk.X, padx=10, pady=(10, 0))

        self.app_id_var = tk.StringVar(value=core.APP_ID or "69db868cc68a628a7d0f207f")
        self.output_dir_var = tk.StringVar(value=core.OUTPUT_DIR or os.path.join(os.getcwd(), "output"))
        self.screenshot_var = tk.BooleanVar(value=core.ENABLE_SCREENSHOTS)
        self.empty_fields_var = tk.BooleanVar(value=core.INCLUDE_EMPTY_FIELD_MAPPINGS)

        # 行0: 应用ID
        ttk.Label(frm, text="应用ID:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        ttk.Entry(frm, textvariable=self.app_id_var, width=40).grid(row=0, column=1, sticky=tk.EW)

        # 行1: 输出目录
        ttk.Label(frm, text="输出目录:").grid(row=1, column=0, sticky=tk.W, padx=(0, 5), pady=(5, 0))
        dir_frm = ttk.Frame(frm)
        dir_frm.grid(row=1, column=1, sticky=tk.EW, pady=(5, 0))
        ttk.Entry(dir_frm, textvariable=self.output_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dir_frm, text="浏览...", width=6,
                   command=lambda: self._pick_dir(self.output_dir_var)).pack(side=tk.LEFT, padx=(5, 0))

        # 行2: 开关
        opt = ttk.Frame(frm)
        opt.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))
        ttk.Checkbutton(opt, text="启用截图", variable=self.screenshot_var).pack(side=tk.LEFT, padx=(0, 20))
        ttk.Checkbutton(opt, text="输出空值字段映射", variable=self.empty_fields_var).pack(side=tk.LEFT)

        frm.columnconfigure(1, weight=1)

    def _build_buttons(self):
        frm = ttk.Frame(self.root)
        frm.pack(fill=tk.X, padx=10, pady=8)

        self.btn_launch  = ttk.Button(frm, text="0. 启动浏览器 (Edge/Chrome)", command=self._on_launch)
        self.btn_connect = ttk.Button(frm, text="1. 连接浏览器 & 获取模块", command=self._on_connect)
        self.btn_start   = ttk.Button(frm, text="2. 开始采集", command=self._on_start, state=tk.DISABLED)
        self.btn_stop    = ttk.Button(frm, text="停止", command=self._on_stop, state=tk.DISABLED)
        for b in (self.btn_launch, self.btn_connect, self.btn_start, self.btn_stop):
            b.pack(side=tk.LEFT, padx=(0, 8))

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(frm, textvariable=self.status_var, foreground="gray").pack(side=tk.RIGHT)

    def _build_panels(self):
        paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        # 模块列表（使用 Checkbutton 勾选框）
        mod_outer = ttk.LabelFrame(paned, text="模块列表（勾选要采集的模块）", padding=5)
        btn_row = ttk.Frame(mod_outer)
        btn_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(btn_row, text="全选", width=6, command=self._select_all).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="清空", width=6, command=self._deselect_all).pack(side=tk.LEFT, padx=(4, 0))

        # 可滚动区域
        canvas = tk.Canvas(mod_outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(mod_outer, orient=tk.VERTICAL, command=canvas.yview)
        self._mod_frame = ttk.Frame(canvas)
        self._mod_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._mod_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        # 鼠标滚轮支持
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-e.delta // 120, "units"))

        self._mod_vars = []  # [(BooleanVar, module_dict), ...]
        paned.add(mod_outer, weight=1)

        # 日志
        log_frm = ttk.LabelFrame(paned, text="日志输出", padding=5)
        self.log = scrolledtext.ScrolledText(log_frm, state="disabled", wrap=tk.WORD, font=("Consolas", 9))
        self.log.pack(fill=tk.BOTH, expand=True)
        paned.add(log_frm, weight=2)

    # ── 通用辅助 ───────────────────────────────────────────────

    @staticmethod
    def _pick_dir(var):
        d = filedialog.askdirectory(initialdir=var.get())
        if d:
            var.set(d)

    def _sync_config(self):
        """将 GUI 配置同步到 core 模块"""
        core.APP_ID = self.app_id_var.get().strip()
        out = self.output_dir_var.get().strip()
        core.OUTPUT_DIR = out or os.path.join(os.getcwd(), "output")
        core.ENABLE_SCREENSHOTS = self.screenshot_var.get()
        core.INCLUDE_EMPTY_FIELD_MAPPINGS = self.empty_fields_var.get()
        os.makedirs(core.OUTPUT_DIR, exist_ok=True)

    def _set_busy(self, busy=True):
        """统一切换按钮状态：busy=True 时禁用主要按钮"""
        state_busy = tk.DISABLED if busy else tk.NORMAL
        self.btn_launch.configure(state=state_busy)
        self.btn_connect.configure(state=state_busy)
        self.btn_start.configure(state=state_busy if self.module_tree else tk.DISABLED)
        self.btn_stop.configure(state=tk.NORMAL if busy else tk.DISABLED)
        self._running = busy

    def _run_async(self, coro):
        """在子线程中运行 asyncio 协程，完成后恢复按钮"""
        def _worker():
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(coro)
            except Exception as e:
                print(f"\n❌ 错误: {e}")
                import traceback
                traceback.print_exc()
            finally:
                loop.close()
                self.root.after(0, self._set_busy, False)

        self._set_busy(True)
        threading.Thread(target=_worker, daemon=True).start()

    # ── 业务逻辑 ───────────────────────────────────────────────

    def _on_launch(self):
        """启动浏览器（Edge/Chrome/Chromium）"""
        from launch_browser import find_browser, check_cdp_running, launch_browser as do_launch, PROFILE_DIR, CDP_PORT, _get_browser_label

        if check_cdp_running():
            print(f"✅ 已有浏览器在 localhost:{CDP_PORT} 运行，无需重复启动")
            return

        result = find_browser()
        if not result:
            messagebox.showerror("未找到浏览器", "请安装以下任一浏览器：\n- Microsoft Edge\n- Google Chrome\n或运行: python -m playwright install chromium")
            return

        browser_name, exe_path = result
        label = _get_browser_label(browser_name)
        print(f"✅ 检测到: {label}\n   路径: {exe_path}")
        os.makedirs(PROFILE_DIR, exist_ok=True)

        print(f"🚀 启动 {label}...")
        proc = do_launch(exe_path, PROFILE_DIR)

        # 等待 CDP 就绪（最多 15 秒）
        self._launch_proc = proc
        self._launch_check_count = 0
        self._launch_label = label

        def _poll():
            from launch_browser import check_cdp_running as _check
            self._launch_check_count += 1
            if _check():
                print(f"✅ {label} 已就绪 (CDP 端口: {CDP_PORT})")
                self.status_var.set(f"{label} 已就绪")
                return
            if self._launch_check_count >= 15:
                print("⚠️ 浏览器启动超时，请检查是否被其他程序阻止")
                return
            self.root.after(1000, _poll)

        self.root.after(1000, _poll)

    def _on_connect(self):
        self.status_var.set("正在连接浏览器...")
        self._run_async(self._do_connect())

    async def _do_connect(self):
        self._sync_config()
        print(f"正在连接浏览器 ({core.CDP_URL})...")

        from launch_browser import check_cdp_running
        if not check_cdp_running():
            print(f"❌ 未检测到浏览器！请先点击「0. 启动浏览器」或手动启动带 --remote-debugging-port={core.CDP_URL.split(':')[-1]} 的浏览器")
            return

        _, _, page = await core.connect()
        print("✓ 浏览器连接成功\n")

        print("正在提取模块树...")
        self.module_tree = await core.build_module_tree(page, core.APP_ID)
        if not self.module_tree:
            print("\n❌ 未能提取到模块树，请确认已在浏览器中打开目标应用")
            return

        self.root.after(0, self._fill_modules)

    def _fill_modules(self):
        # 清空旧的 Checkbutton
        for w in self._mod_frame.winfo_children():
            w.destroy()
        self._mod_vars.clear()

        for mod in self.module_tree:
            valid = _count_valid(mod)
            total = len(mod.get('children', []))
            var = tk.BooleanVar(value=False)
            text = f"{mod['name']}  ({valid}/{total})"
            cb = ttk.Checkbutton(self._mod_frame, text=text, variable=var)
            cb.pack(anchor=tk.W, pady=1)
            self._mod_vars.append((var, mod))

        self.btn_start.configure(state=tk.NORMAL)
        self.status_var.set(f"已获取 {len(self.module_tree)} 个模块，请勾选后点击「开始采集」")
        print(f"\n✓ 共 {len(self.module_tree)} 个模块，请勾选后点击「开始采集」")

    def _select_all(self):
        for var, _ in self._mod_vars:
            var.set(True)

    def _deselect_all(self):
        for var, _ in self._mod_vars:
            var.set(False)

    def _on_start(self):
        selected = [mod for var, mod in self._mod_vars if var.get()]
        if not selected:
            messagebox.showwarning("提示", "请先勾选至少一个模块")
            return

        print(f"\n已选择模块: {', '.join(m['name'] for m in selected)}")
        self._sync_config()
        self.status_var.set("采集运行中...")
        self._run_async(self._do_capture(selected))

    async def _do_capture(self, modules):
        _, _, page = await core.connect()
        all_results = {}

        for mi, module in enumerate(modules):
            print(f"\n{'='*60}")
            print(f"模块 {mi+1}/{len(modules)}: {module['name']}")
            print(f"{'='*60}")
            try:
                name, summaries = await core.run_module(page, module, output_root=core.OUTPUT_DIR)
                all_results[name] = summaries
            except Exception as e:
                print(f"  ❌ 模块处理错误: {e}")
                import traceback
                traceback.print_exc()

        self._write_summary(all_results)

    def _write_summary(self, all_results):
        """生成全局汇总 JSON 并打印完成信息"""
        total_forms = sum(len(v) for v in all_results.values())
        total_ast = sum(f.get('assistantCount', 0) for forms in all_results.values() for f in forms)

        if all_results:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            payload = {
                "appId": core.APP_ID,
                "version": "8.1",
                "generatedAt": datetime.now().isoformat(),
                "stats": {"modules": len(all_results), "forms": total_forms, "totalAssistants": total_ast},
                "modules": all_results,
            }
            path = os.path.join(core.OUTPUT_DIR, f"GLOBAL_SUMMARY_{ts}.json")
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"  📋 全局JSON: {path}")

        print(f"\n{'='*60}")
        print(f"✅ 采集完成!  模块: {len(all_results)}  表单: {total_forms}  助手: {total_ast}")
        print(f"   输出目录: {core.OUTPUT_DIR}")
        print(f"{'='*60}")
        self.root.after(0, lambda: self.status_var.set("采集完成"))

    def _on_stop(self):
        self._running = False
        self.status_var.set("正在停止...")
        print("\n⚠️ 已请求停止，当前模块完成后结束")


# ── 入口 ──────────────────────────────────────────────────────

if __name__ == '__main__':
    root = tk.Tk()
    CaptureGUI(root)
    root.mainloop()
