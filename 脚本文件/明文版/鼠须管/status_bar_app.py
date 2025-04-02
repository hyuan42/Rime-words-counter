"""

版本: 字数统计工具-鼠须管明文版1.0
作者: hyuan
Github:https://github.com/hyuan42/Rime-words-counter

脚本功能：把处理好的数据（也就是json文档）展示在macOS顶部状态栏。

使用前，记得安装以下依赖库：
pip install rumps portalocker watchdog schedule

"""

import json
import rumps
import subprocess
import os
import sys
import time
import portalocker
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ========== 统一路径配置 ==========
# 定义文件夹路径。注意，这里只要文件夹就好。
CUSTOM_PATH = "/Users/iCloud/Example"  #字数统计数据的json文档存放的文件夹的路径。想要多设备同步数据的，把这个json文件放到同步网盘好一点
CUSTOM_PATH2 = "/Users/你的设备名/Library/Rime/py_wordscounter"  #放置Python脚本和读取csv文档的路径，跟lua脚本里的路径要一致

# 定义文件路径
JSON_FILE = os.path.join(CUSTOM_PATH, "words_count_history.json")  #读取核心json数据文档，是我们记录每天/月/年/总输入字数的文档
SIGNAL_FILE = os.path.join(CUSTOM_PATH2, ".show_gui_signal")  #信号文件。用来和status_bar_app.py通信的，把今日字数显示在macOS顶部状态栏(menus bar)上
STAT_GUI_SCRIPT = os.path.join(CUSTOM_PATH2, "words_counter.py")  #调用详细数据的主界面


#具体用处看words_counter.py里的注释吧
CURRENT_SYSTEM = "last_processed_row_mac"

# 确保目录存在
if not os.path.exists(CUSTOM_PATH):
    os.makedirs(CUSTOM_PATH)

# ========== 安全文件访问函数 ==========
def safe_file_access(file_path, mode, retries=3, delay=0.1):
    """跨平台安全文件访问"""
    for attempt in range(retries):
        try:
            f = open(file_path, mode, encoding='utf-8')
            # 根据模式选择锁类型
            lock_type = portalocker.LOCK_SH if 'r' in mode else portalocker.LOCK_EX
            portalocker.lock(f, lock_type)
            return f
        except (IOError, portalocker.LockException) as e:
            if attempt == retries - 1:
                raise
            time.sleep(delay)
    raise Exception(f"文件访问失败: {file_path}")

# ========== 文件监控处理类 ==========
# 监控json文件，当json文件有变动（有新增字数），才会处理/更新数据到前端GUI界面
class JSONHandler(FileSystemEventHandler):
    def __init__(self, app):
        self.app = app
        self.last_trigger = 0  # 防抖动时间戳

    def on_modified(self, event):
        if event.src_path == JSON_FILE and time.time() - self.last_trigger > 0.5:
            self.last_trigger = time.time()
            self.app.update_title()

# ========== 状态栏应用类 ==========
class StatusBarApp(rumps.App):
    def __init__(self):
        super().__init__("📊 加载中...")
        self.menu = ["打开详细数据", "手动清除csv", "退出",None]
        self.gui_process = None
        
        # 初始化文件监控
        self.observer = Observer()
        self.observer.schedule(
            JSONHandler(self),
            path=os.path.dirname(JSON_FILE),
            recursive=False
        )
        self.observer.start()
        
        # 启动后台进程
        self.start_gui_background()
        
        # 等待GUI初始化
        time.sleep(0.5)
        self.update_title()

    def start_gui_background(self):
        """启动GUI后台进程"""
        if not self.is_gui_running():
            self.gui_process = subprocess.Popen(
                [sys.executable, STAT_GUI_SCRIPT],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )

    def is_gui_running(self):
        """改进的进程检查"""
        try:
            return self.gui_process and self.gui_process.poll() is None
        except ProcessLookupError:
            return False

    @rumps.clicked("打开详细数据")
    def open_gui(self, _):
        """触发显示窗口"""
        try:
            with safe_file_access(SIGNAL_FILE, 'w') as f:  # 安全写入信号文件
                f.write('show')
        except Exception as e:
            print(f"信号文件写入失败: {e}")
        if not self.is_gui_running():
            self.start_gui_background()

    @rumps.clicked("手动清除csv")
    def clear_csv_manual(self, _):
        """安全清除CSV文件"""
        try:
            # 调用主程序的清理方法
            subprocess.call([sys.executable, STAT_GUI_SCRIPT, "--clear-csv"])
            self.update_title()
            rumps.notification("字数统计", "操作完成", "CSV文件已清空")
        except Exception as e:
            rumps.notification("字数统计", "操作失败", str(e), sound=True)

    @rumps.clicked("退出")
    def quit(self, _):
        """安全退出整个应用"""
        try:
            # 终止GUI进程
            if self.is_gui_running():
                # macOS需要终止进程树
                subprocess.run(['pkill', '-f', STAT_GUI_SCRIPT], check=True)
                self.gui_process = None

            # 停止文件监控
            self.observer.stop()
            self.observer.join(timeout=2)
            
            # 清理信号文件
            if os.path.exists(SIGNAL_FILE):
                with safe_file_access(SIGNAL_FILE, 'w') as f:
                    f.truncate(0)
                os.remove(SIGNAL_FILE)
                
        except Exception as e:
            print(f"退出时发生错误: {e}")
        finally:
            rumps.quit_application()

    def update_title(self):
        """带重试机制的标题更新"""
        for _ in range(3):  # 最大重试3次
            try:
                with safe_file_access(JSON_FILE, 'r') as f:  # 安全读取
                    data = json.load(f)
                    today = data['daily'].get(datetime.now().strftime('%Y-%m-%d'), 0)
                    self.title = f"📝 {today}字"
                    return
            except (json.JSONDecodeError, IOError) as e:
                print(f"读取失败: {e}")
                time.sleep(0.1)
            except Exception as e:
                print(f"意外错误: {e}")
                break
        self.title = "❌ 数据异常"


if __name__ == "__main__":
    app = StatusBarApp()
    app.run()
