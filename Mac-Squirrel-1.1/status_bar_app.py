"""
版本: 字数统计工具-鼠须管-状态栏-v1.1
作者: hyuan
Github仓库: https://github.com/hyuan42/Rime-words-counter
时间: 2026-06-26

脚本功能：在顶部状态栏显示字数，并提供打开详细数据、配置文件夹与日志、手动清除CSV、设置等功能。

依赖：pip install rumps portalocker watchdog
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
from wc_core import (  # noqa: E402
    DataProcessor, app_support_dir, bus, config_path, is_frozen,
    load_config, log_path, schedule_daily, setup_logger,
)

config = load_config()
JSON_FILE = str(config.json_path)
CSV_FILE = str(config.csv_path)
DEVICE_ID = config.device_id

# 开关：是否启用后台轮询（兜底 watchdog 失败）
# 设为 False 可完全依赖 watchdog，节省 0.011% CPU（几乎无感）
ENABLE_POLLER = True

processor = DataProcessor(CSV_FILE, JSON_FILE, DEVICE_ID)


def _reload_globals_from_config():
    """重新加载 config，更新全局变量。返回 (paths_changed, schedule_changed) 元组。

    paths_changed: csv/json/device_id 变了 → 需要重建 processor + observer
    schedule_changed: 清理开关或时间变了 → 需要重启定时器
    """
    global config, JSON_FILE, CSV_FILE, DEVICE_ID, processor
    new_cfg = load_config()
    new_json = str(new_cfg.json_path)
    new_csv = str(new_cfg.csv_path)
    new_dev = new_cfg.device_id

    paths_changed = (new_json, new_csv, new_dev) != (JSON_FILE, CSV_FILE, DEVICE_ID)
    schedule_changed = (
        bool(config.auto_clear_csv) != bool(new_cfg.auto_clear_csv)
        or config.clear_hour != new_cfg.clear_hour
        or config.clear_minute != new_cfg.clear_minute
        or config.clear_interval_days != new_cfg.clear_interval_days
    )

    config = new_cfg
    if paths_changed:
        JSON_FILE, CSV_FILE, DEVICE_ID = new_json, new_csv, new_dev
        processor = DataProcessor(CSV_FILE, JSON_FILE, DEVICE_ID)
        print(f"[config] 路径热重载: json={JSON_FILE}, csv={CSV_FILE}, device={DEVICE_ID}")
    if schedule_changed:
        print(f"[config] 定时清理设置变更: enabled={new_cfg.auto_clear_csv}, "
              f"interval={new_cfg.clear_interval_days}天, "
              f"time={new_cfg.clear_hour:02d}:{new_cfg.clear_minute:02d}")
    return paths_changed, schedule_changed


# ============================================================
#  GUI 模式入口（被 subprocess 调起来时走这里）
# ============================================================
def run_gui_mode():
    """子进程模式：拉起 Tk 主窗口（详细数据）。Tk 占用主线程，符合 macOS 要求。"""
    setup_logger("gui")
    from words_counter import Application
    app = Application()
    # 子进程模式下点叉直接退出（不再 withdraw 隐藏）
    app.protocol("WM_DELETE_WINDOW", app.full_exit)
    app.deiconify()
    app.lift()
    app.attributes("-topmost", True)
    app.after(200, lambda: app.attributes("-topmost", False))
    app.mainloop()


def run_settings_mode():
    """子进程模式：只开设置窗口，用独立的最小 Tk root（不启动 Application 那一套）。"""
    setup_logger("settings")
    import tkinter as tk
    from words_counter import SettingsWindow, Theme

    root = tk.Tk()
    root.withdraw()                 # 隐藏 root，只显示 SettingsWindow
    root.configure(bg=Theme.BG)

    # standalone=True：不做 transient（transient 到隐藏 root 会让自己也不显示）
    sw = SettingsWindow(root, standalone=True)
    sw.protocol("WM_DELETE_WINDOW", lambda: (sw.destroy(), root.destroy()))
    sw.bind("<Destroy>", lambda e: root.destroy() if e.widget is sw else None)

    # 居中 + 置顶，确保窗口真的弹到用户眼前
    sw.update_idletasks()
    sw_w, sw_h = 560, 600
    screen_w = sw.winfo_screenwidth()
    screen_h = sw.winfo_screenheight()
    x = (screen_w - sw_w) // 2
    y = (screen_h - sw_h) // 3
    sw.geometry(f"{sw_w}x{sw_h}+{x}+{y}")
    sw.deiconify()
    sw.lift()
    sw.focus_force()
    sw.attributes("-topmost", True)
    sw.after(300, lambda: sw.attributes("-topmost", False))

    root.mainloop()


# ============================================================
#  状态栏模式（默认入口，rumps 占用主线程）
# ============================================================
import rumps  # 延迟到这里 import，避免 GUI 模式无谓加载  # noqa: E402
from watchdog.events import FileSystemEventHandler, PatternMatchingEventHandler  # noqa: E402
from watchdog.observers import Observer  # noqa: E402


class CSVHandler(PatternMatchingEventHandler):
    """监听 CSV 文件变化，触发处理。"""
    def __init__(self, csv_file):
        super().__init__(patterns=[csv_file], ignore_directories=True)
        self.last_trigger = 0.0

    def on_modified(self, event):
        now = time.time()
        if now - self.last_trigger > 0.3:  # 防抖
            self.last_trigger = now
            try:
                processor.process_data()  # 会自动 bus.emit 触发更新
                print(f"[CSV watchdog] 处理完成 @ {datetime.now().strftime('%H:%M:%S')}")
            except Exception as e:
                print(f"[CSV watchdog] 处理失败: {e}")


class JSONHandler(PatternMatchingEventHandler):
    def __init__(self, app, json_file):
        super().__init__(patterns=[json_file], ignore_directories=True)
        self.app = app
        self.last_trigger = 0.0

    def on_modified(self, event):
        now = time.time()
        if now - self.last_trigger > 0.5:
            self.last_trigger = now
            self.app.update_title()


class ConfigHandler(FileSystemEventHandler):
    """监听 config.json 变化 → 热重载（设置窗口保存后无需重启）。

    用 FileSystemEventHandler 而不是 PatternMatchingEventHandler，
    手动比对路径，更可靠（PatternMatchingEventHandler 的 patterns 用 fnmatch glob，
    某些情况下匹配不准）。
    """
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.target = os.path.realpath(str(config_path()))
        self.last_trigger = 0.0

    def _is_target(self, path: str) -> bool:
        try:
            return os.path.realpath(path) == self.target
        except OSError:
            return False

    def on_modified(self, event):
        if event.is_directory or not self._is_target(event.src_path):
            return
        now = time.time()
        if now - self.last_trigger > 0.5:
            self.last_trigger = now
            print(f"[config] 检测到 config.json 修改 @ {datetime.now().strftime('%H:%M:%S')}")
            self.app.on_config_changed()

    def on_created(self, event):
        # 设置窗口可能是先 delete 再 create（atomic write），所以 create 也要处理
        self.on_modified(event)


class StatusBarApp(rumps.App):
    def __init__(self):
        super().__init__("📊 加载中...", quit_button=None)  # 禁用 rumps 默认的 Quit
        self.menu = [
            "打开详细数据",
            "配置文件夹与日志",
            "手动清除CSV",
            "设置…",
            None,
            "GitHub",
            "退出",
        ]
        self._gui_proc: subprocess.Popen | None = None
        self._settings_proc: subprocess.Popen | None = None
        self._poller_running = True
        self._scheduler = None  # CSV 定时清理器

        # 启动时把积压的 CSV 处理一次
        try:
            processor.process_data()
        except Exception as e:
            print(f"[startup] 数据处理失败: {e}")

        # watchdog 监听（CSV + JSON + config）
        self.observer = None
        self._setup_observer()

        bus.subscribe(lambda _payload: self.update_title())
        self.update_title()

        # 后台轮询线程（兜底：watchdog 在某些系统上不可靠）
        if ENABLE_POLLER:
            self._start_csv_poller()
        else:
            print("[poller] 已禁用，完全依赖 watchdog")

        # 定时清理 CSV（若启用）
        self._start_auto_clear_scheduler()

    def _setup_observer(self):
        """（重新）搭建 watchdog 监听。热重载后调用以指向新路径。"""
        # 关掉旧的
        if self.observer is not None:
            try:
                self.observer.stop()
                self.observer.join(timeout=2)
            except Exception as e:
                print(f"[watchdog] 关闭旧 observer 失败: {e}")

        self.observer = Observer()

        csv_dir = os.path.dirname(CSV_FILE) or "."
        if os.path.isdir(csv_dir):
            self.observer.schedule(CSVHandler(CSV_FILE), path=csv_dir, recursive=False)
            print(f"[watchdog] 监听 CSV: {csv_dir}")

        json_dir = os.path.dirname(JSON_FILE) or "."
        if os.path.isdir(json_dir):
            self.observer.schedule(JSONHandler(self, JSON_FILE), path=json_dir, recursive=False)
            print(f"[watchdog] 监听 JSON: {json_dir}")

        # config.json 监听 → 热重载
        cfg_dir = os.path.dirname(str(config_path())) or "."
        if os.path.isdir(cfg_dir):
            self.observer.schedule(ConfigHandler(self), path=cfg_dir, recursive=False)
            print(f"[watchdog] 监听 config: {cfg_dir}")

        self.observer.start()

    def on_config_changed(self):
        """config.json 被改写（设置窗口保存）后，热重载 processor / 定时器。"""
        try:
            paths_changed, schedule_changed = _reload_globals_from_config()
            if paths_changed:
                self._setup_observer()       # observer 指向新路径
                processor.process_data()     # 立即处理一次新路径的数据
                self.update_title()
            if schedule_changed:
                self._start_auto_clear_scheduler()  # 重启定时器
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[config] 热重载失败: {e}")

    # -------- 主窗口（独立子进程） --------
    def _gui_running(self) -> bool:
        try:
            return self._gui_proc is not None and self._gui_proc.poll() is None
        except (ProcessLookupError, OSError):
            return False

    def _entry_executable(self) -> list:
        """返回启动子进程要用的命令前缀（处理 frozen / 开发两种情况）。"""
        if is_frozen():
            # py2app 打包后，sys.argv[0] 指向脚本本体（.py 无执行权限），
            # 不能直接 exec。EXECUTABLE_PATH 指向 .app/Contents/MacOS/字数统计
            entry = os.environ.get("EXECUTABLE_PATH")
            if not entry or not os.path.exists(entry):
                resources = os.path.dirname(os.path.realpath(sys.argv[0]))
                contents = os.path.dirname(resources)  # .../Contents
                macos = os.path.join(contents, "MacOS")
                candidates = [
                    os.path.join(macos, name) for name in os.listdir(macos)
                    if name != "python"
                ]
                entry = candidates[0] if candidates else sys.argv[0]
            return [entry, "--gui"]
        return [sys.executable, os.path.abspath(__file__), "--gui"]

    def _popen(self, cmd: list) -> subprocess.Popen:
        """启动子进程。子进程通过 setup_logger 自己写日志到 app.log，
        这里 stdout/stderr 直接 DEVNULL（防止 buffer 满）。"""
        print(f"[spawn] {cmd}")  # 状态栏侧记录一下
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _spawn_gui(self, extra_args=None):
        """启动详细数据窗口子进程（已有则前置，不重开）。"""
        if self._gui_running():
            self._activate_gui_window()
            return
        cmd = self._entry_executable() + (extra_args or [])
        self._gui_proc = self._popen(cmd)

    def _spawn_settings(self):
        """启动设置窗口子进程（独立追踪，已有则前置不重开）。"""
        if self._settings_proc is not None and self._settings_proc.poll() is None:
            # 已有设置窗口在跑，前置到最前
            try:
                subprocess.run(
                    ["osascript", "-e",
                     'tell application "System Events" to set frontmost of '
                     '(first process whose unix id is '
                     + str(self._settings_proc.pid) + ') to true'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            return
        cmd = self._entry_executable() + ["--settings"]
        self._settings_proc = self._popen(cmd)

    def _activate_gui_window(self):
        """通过 AppleScript 把 GUI 子进程的窗口激活到屏幕最前。"""
        try:
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to set frontmost of '
                 '(first process whose unix id is ' + str(self._gui_proc.pid) + ') to true'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    # -------- 菜单 --------
    @rumps.clicked("打开详细数据")
    def open_gui(self, _):
        self._spawn_gui()

    @rumps.clicked("设置…")
    def open_settings(self, _):
        self._spawn_settings()

    @rumps.clicked("配置文件夹与日志")
    def open_config_and_logs(self, _):
        """打开配置/日志所在的文件夹。
        里面包含 config.json（配置）、app.log（最新日志）、app.log.1~.3（历史）
        以及 words_count_history.json（默认数据位置）。
        如果出现 bug，把整个 app.log 发给开发者即可。"""
        folder = app_support_dir()
        folder.mkdir(parents=True, exist_ok=True)
        # 在 Finder 里高亮选中 app.log，方便用户直接看到日志文件
        lp = log_path()
        if lp.exists():
            os.system(f'open -R "{lp}"')
        else:
            os.system(f'open "{folder}"')

    @rumps.clicked("手动清除CSV")
    def clear_csv_manual(self, _):
        try:
            processor.process_data()
            ok = processor.clear_csv()
            self.update_title()
            if ok:
                rumps.notification("字数统计", "操作完成", "CSV 文件已清空")
            else:
                rumps.notification("字数统计", "操作失败", "详见日志", sound=True)
        except Exception as e:
            rumps.notification("字数统计", "操作失败", str(e), sound=True)

    @rumps.clicked("GitHub")
    def open_github(self, _):
        os.system('open "https://github.com/hyuan42/Rime-words-counter"')

    @rumps.clicked("退出")
    def quit(self, _):
        try:
            self._poller_running = False  # 停止后台轮询线程
            for proc in (self._gui_proc, self._settings_proc):
                if proc is not None and proc.poll() is None:
                    try:
                        proc.terminate()
                    except OSError:
                        pass
            self.observer.stop()
            self.observer.join(timeout=2)
        except Exception as e:
            print(f"退出时发生错误: {e}")
        finally:
            rumps.quit_application()

    # -------- CSV 后台轮询（兜底，watchdog 不可靠时用）--------
    def _start_csv_poller(self):
        """后台线程每 1.5 秒轮询一次 CSV，防止 watchdog 静默失败。"""
        def poll_loop():
            while self._poller_running:
                try:
                    processor.process_data()
                except Exception as e:
                    print(f"[poller] CSV 处理失败: {e}")
                time.sleep(1.5)

        t = threading.Thread(target=poll_loop, daemon=True, name="csv-poller")
        t.start()
        print("[poller] 后台轮询线程已启动（1.5s 间隔）")

    def _start_auto_clear_scheduler(self):
        """启动 CSV 定时清理（若配置启用）。"""
        # 先停掉旧的（热重载时）
        if self._scheduler is not None:
            self._scheduler.stop()
            self._scheduler = None

        cfg = load_config()
        if not cfg.auto_clear_csv:
            print("[scheduler] CSV 定时清理未启用")
            return

        def clear_csv_daily():
            try:
                processor.process_data()  # 先处理积压数据
                processor.clear_csv()
                print(f"[scheduler] CSV 已清空 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            except Exception as e:
                print(f"[scheduler] 清空失败: {e}")

        self._scheduler = schedule_daily(
            clear_csv_daily,
            hour=cfg.clear_hour, minute=cfg.clear_minute,
            interval_days=cfg.clear_interval_days,
        )
        print(f"[scheduler] CSV 定时清理已启动，每隔 {cfg.clear_interval_days} 天的 "
              f"{cfg.clear_hour:02d}:{cfg.clear_minute:02d} 执行")

    # -------- 标题 --------
    def update_title(self):
        for _ in range(3):
            try:
                agg = processor.aggregate()
                today = agg["daily"].get(datetime.now().strftime("%Y-%m-%d"), 0)
                self.title = f"📝 {today:,}"
                return
            except Exception as e:
                print(f"读取失败: {e}")
                time.sleep(0.1)
        self.title = "❌ 数据异常"


# ============================================================
#  入口分发
# ============================================================
if __name__ == "__main__":
    if "--settings" in sys.argv:
        run_settings_mode()
    elif "--gui" in sys.argv:
        run_gui_mode()
    else:
        setup_logger("statusbar")
        StatusBarApp().run()
