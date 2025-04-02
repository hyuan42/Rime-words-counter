"""

版本: 字数统计工具-小狼毫字数版1.0
作者: hyuan
Github:https://github.com/hyuan42/Rime-words-counter

脚本功能：处理LUA脚本记录的字数数据（不记录明文，只记录上屏的汉字个数），统计汇总到json文件，并在系统托盘创建图标、创建一个悬浮窗口实时展示今日字数，同时还具有测速功能、查看历史统计字数的功能等。
运行脚本需要安装Python环境，使用前，记得安装以下依赖库：
pip install portalocker pystray pillow pywin32 watchdog schedule

自启动、后台静默运行等功能请自行查询或询问DeepSeek“Python脚本自启动、后台运行”等问题~

"""

from collections import defaultdict
import portalocker
import os
import json
import csv
import sys
import time
import threading
import pystray
from PIL import Image, ImageDraw, ImageFont
import win32api
import win32gui
import win32con
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import schedule
import tkinter as tk
from tkinter import ttk, messagebox

# ================== 路径配置 ==================
CSV_FILE = r'C:\Users\你的用户名\AppData\Roaming\Rime\py_wordscounter\words_input.csv' #你在words_counter.lua中设置的存放words_input.csv的位置。改前面路径名就好，不要动csv的文件名。这里win的路径使用单个反斜杠。
JSON_FILE = r'G:\example\words_count_history.json' #用于保存字数统计数据的json文档的路径。想要多设备同步数据的，把这个json文件放到同步网盘好一点。改前面路径名就好，不要动的json文件名

#多设备时，每个设备命名要不一样，而且记得打开words_count_history.json手动修改，有多少个设备，就添加多少行last_processed_row_其他设备名。
#用处: 记录本设备的csv文档里最后一行的行数到json中，每次打字时，csv文档有新增的行数，Python脚本就会从记录的行数开始算新增行数的字数们，达到增量统计，而不是每次都需要从第一行算到最后一行。
CURRENT_SYSTEM = "last_processed_row_win"


# ================== 安全文件访问函数 ==================
def safe_file_access(file_path, mode, retries=3, delay=0.1):
    """安全文件访问（自动重试 + 文件锁）"""
    for attempt in range(retries):
        f = None
        try:
            f = open(file_path, mode, encoding='utf-8')
            portalocker.lock(f, portalocker.LOCK_EX)
            return f
        except (IOError, portalocker.LockException) as e:
            if f is not None:
                f.close()
            if attempt == retries - 1:
                raise Exception(f"文件访问失败: {file_path} (最终错误: {e})")
            time.sleep(delay)

# ================== 系统托盘图标类 ==================
class SysTrayManager:
    def __init__(self, master):
        self.master = master
        self.icon = None
        self.running = True
        self.create_tray_icon()

    def create_tray_icon(self):
        # 生成图标
        size = 64
        image = Image.new('RGBA', (size, size), (0, 0, 0, 0))  # 透明背景
        dc = ImageDraw.Draw(image)
        
        # 绘制图标
        box = [(0, 0), (size-1, size-1)]  
        dc.rounded_rectangle(
            box,
            radius=15,       # 图标圆角
            fill="#1660f5",  # 图标的背景色，想改什么颜色都可以。这里默认设置了蓝色🟦
            outline=None,    # 无边框
            width=0
        )
        
        # 添加白色文字
        try:
            font = ImageFont.truetype("msyh.ttc", 48)  # 微软雅黑。可以指定你要的字体，还有字号
        except IOError:
            font = ImageFont.load_default()
        text = "字" # 系统托盘图标里显示的字，想改成什么字都可以，最好是单字
        bbox = dc.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        position = (
            (size - text_width) // 2,
            (size - text_height) // 2 - 10  # 垂直居中
        )
        dc.text(position, text, fill="white", font=font)  # 色值文字的色值为白色

        # 右键系统托盘图标时出现的菜单结构
        menu_items = [
            pystray.MenuItem('显示主界面', self.show_main_window),
            pystray.MenuItem('切换悬浮窗', self.toggle_taskbar_window),
            pystray.MenuItem('手动清理CSV', self.clear_csv_manually),
            pystray.MenuItem('退出', self.exit_app)
        ]
        
        menu = pystray.Menu(*menu_items)

        # 创建系统托盘图标
        self.icon = pystray.Icon(
            "word_counter",
            image,
            "字数统计工具 by hyuan",
            menu 
        )

        # 启动托盘图标线程
        threading.Thread(target=self.icon.run, daemon=True).start()

    def clear_csv_manually(self, icon, item):
        # 手动清空CSV文件
        try:
            with safe_file_access(CSV_FILE, 'w') as f:
                f.write("timestamp,chinese_count\n")
            # 重置JSON中的last_processed_row_win的行数记录
            with safe_file_access(JSON_FILE, 'r+') as f:
                stats = json.load(f)
                stats[CURRENT_SYSTEM] = 0
                f.seek(0)
                json.dump(stats, f, indent=2, ensure_ascii=False)
                f.truncate()
            messagebox.showinfo("成功", "CSV文件已清空！")
        except Exception as e:
            messagebox.showerror("错误", f"清空失败: {e}")
        
    def show_main_window(self, icon, item):
        """显示主窗口"""
        self.master.deiconify()
        self.master.attributes('-topmost', 1) 
        self.master.attributes('-topmost', 0)

    def toggle_taskbar_window(self, icon, item):
        """切换悬浮窗显示状态"""
        if self.master.taskbar.visible:
            self.master.taskbar.hide_window()
        else:
            self.master.taskbar.show_window()

    def exit_app(self, icon, item):
        """退出程序"""
        self.running = False
        self.icon.stop()
        # 关闭悬浮窗前停止消息循环
        if hasattr(self.master, 'taskbar'):
            self.master.taskbar.running = False
            self.master.taskbar._exit_app()
        # 关闭主窗口
        self.master.destroy()
        os._exit(0)


# ================== 悬浮窗口类 ==================
# 可拖动到任意位置。
# 本来想做成直接展示在任务栏上的，可惜需要C语言，纯用Python好像无法实现。
class TaskbarWindow:
    def __init__(self, master):
        self.master = master
        self.hwnd = None
        self.running = True
        self.visible = True
        self._create_window()
        self._start_message_loop()
        self._start_update_thread()

    def _start_message_loop(self):
        def message_loop():
            while self.running:
                win32gui.PumpWaitingMessages()
                time.sleep(0.1)
        threading.Thread(target=message_loop, daemon=True).start()

    def _start_update_thread(self):
        def update_loop():
            while self.running:
                try:
                    current_modified = os.path.getmtime(JSON_FILE)
                    if current_modified > getattr(self, 'last_modified', 0):
                        self.last_modified = current_modified
                        with safe_file_access(JSON_FILE, 'r') as f:
                            stats = json.load(f)
                        daily = stats['daily'].get(datetime.now().strftime('%Y-%m-%d'), 0)
                        win32gui.SetWindowText(self.hwnd, f"今日字数：{daily}字")
                except Exception as e:
                    print(f"更新任务栏失败: {e}")
                time.sleep(1) 
        threading.Thread(target=update_loop, daemon=True).start()

    def _wnd_proc(self, hwnd, msg, wParam, lParam):
        """窗口消息处理函数"""
        if msg == win32con.WM_CLOSE:
            self.hide_window()
            return 0 
        elif msg == win32con.WM_LBUTTONDBLCLK:
            self.master.deiconify()
        elif msg == win32con.WM_RBUTTONDOWN:
            self._show_context_menu()
        elif msg == win32con.WM_COMMAND:
            if wParam == 1001:
                self.master.deiconify()
            elif wParam == 1002:
                self._exit_app()
        return win32gui.DefWindowProc(hwnd, msg, wParam, lParam)

    def _create_window(self):
        """窗口创建方法"""
        # 1. 注册窗口类
        wnd_class = win32gui.WNDCLASS()
        wnd_class.lpszClassName = "TaskbarCounter"
        wnd_class.hInstance = win32api.GetModuleHandle(None)
        wnd_class.lpfnWndProc = self._wnd_proc
        self.class_atom = win32gui.RegisterClass(wnd_class)

        # 2. 设置窗口样式
        ex_style = win32con.WS_EX_LAYERED | win32con.WS_EX_TOOLWINDOW
        style = win32con.WS_OVERLAPPED | win32con.WS_SYSMENU

        # 3. 创建窗口
        self.hwnd = win32gui.CreateWindowEx(
            ex_style,
            self.class_atom,
            "今日字数：初始化...",
            style,
            0, 0, 100, 100,
            0, 0,
            wnd_class.hInstance,
            None
        )

        # 4. 设置窗口位置（右下角）
        screen_width = win32api.GetSystemMetrics(0)
        screen_height = win32api.GetSystemMetrics(1)
        win32gui.SetWindowPos(
            self.hwnd,
            win32con.HWND_TOPMOST,
            screen_width - 200,
            screen_height - 100,
            180,
            40,
            win32con.SWP_SHOWWINDOW
        )

        # 5. 设置透明度
        win32gui.SetLayeredWindowAttributes(self.hwnd, 0, 255, win32con.LWA_ALPHA)

    def hide_window(self):
        """隐藏窗口"""
        if self.hwnd:
            win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)
            self.visible = False
            print("悬浮窗已隐藏") 

    def show_window(self):
        """显示窗口（新增重建逻辑）"""
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            # 如果窗口已被销毁，重建窗口
            self._create_window()
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOW)
        self.visible = True
        print("悬浮窗已显示") 

    def _exit_app(self):
        """安全退出程序"""
        self.running = False
        if self.hwnd:
            # 用PostMessage通知窗口自行关闭
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
        # 增加短暂延迟确保消息处理
        time.sleep(0.1)
        # 主窗口销毁
        self.master.destroy()
        os._exit(0)

# ================== 数据处理类 ==================
# 读取、处理csv文档里的数据，汇总到json文档中
class DataProcessor:
    update_callback = None
    @classmethod
    def set_update_callback(cls, callback):
        cls.update_callback = callback
        
    @staticmethod
    def process_data():
        if not os.path.exists(JSON_FILE):
            with safe_file_access(JSON_FILE, 'w') as f:
                json.dump({
                    "daily": {},
                    "monthly": {},
                    "yearly": {},
                    "total": 0,
                    "last_processed_row_win": 0
                }, f, indent=2, ensure_ascii=False)

        stats = {"daily": {}, "monthly": {}, "yearly": {}, "total": 0, "last_processed_row_win": 0}
        try:
            with safe_file_access(JSON_FILE, 'r+') as f:
                if os.path.getsize(JSON_FILE) > 0:
                    stats.update(json.load(f))
                
                new_data = []
                if os.path.exists(CSV_FILE):
                    with safe_file_access(CSV_FILE, 'r') as csv_f:
                        reader = csv.reader(csv_f)
                        try:
                            if next(reader) != ['timestamp', 'chinese_count']:
                                return
                        except StopIteration:
                            return
                        
                        start_row = stats.get(CURRENT_SYSTEM, 0)
                        for _ in range(start_row):
                            next(reader, None)
                        
                        for row in reader:
                            if len(row) < 2:
                                continue
                            try:
                                new_data.append({
                                    "timestamp": row[0],
                                    "chinese_count": int(row[1])
                                })
                            except ValueError:
                                continue
                        
                        stats[CURRENT_SYSTEM] = start_row + len(new_data)
                
                for entry in new_data:
                    dt = datetime.fromisoformat(entry['timestamp'])
                    day_key = dt.strftime('%Y-%m-%d')
                    month_key = dt.strftime('%Y-%m')
                    year_key = dt.strftime('%Y')
                    count = entry['chinese_count']
                    
                    stats["daily"][day_key] = stats["daily"].get(day_key, 0) + count
                    stats["monthly"][month_key] = stats["monthly"].get(month_key, 0) + count
                    stats["yearly"][year_key] = stats["yearly"].get(year_key, 0) + count
                    stats["total"] += count
                if DataProcessor.update_callback:
                    DataProcessor.update_callback(stats["total"])
                
                
                f.seek(0)
                json.dump(stats, f, indent=2, ensure_ascii=False)
                f.truncate()

        except Exception as e:
            print(f"数据处理错误: {e}")

# ================== 文件监控类 ==================
# 监控csv文件，当csv文件有修改（也就是有打字时会新增行数），才会处理/更新数据到json
class CSVHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path == CSV_FILE:
            time.sleep(1)
            DataProcessor.process_data()
            if hasattr(gui, 'update_display'):
                gui.update_display()

# ================== 测速功能类 ==================
class SpeedTester:
    def __init__(self, master):
        self.master = master
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.start_time = None
            self.start_count = 0
            self.last_speed = 0.0
            self.active = False

    def start(self):
        try:
            with safe_file_access(JSON_FILE, 'r') as f:
                stats = json.load(f)
                initial_count = stats['total']
        except Exception as e:
            print(f"测速初始化失败: {e}")
            initial_count = self.master.current_total_count
        with self.lock:
            self.active = True
            self.start_time = datetime.now()
            self.start_count = initial_count

    def get_speed(self, force_refresh=False):
        if not self.active:
            return 0
        
        # 异步获取当前字数（避免阻塞主线程）
        def _get_current_count():
            try:
                with safe_file_access(JSON_FILE, 'r') as f:
                    stats = json.load(f)
                    return stats['total']
            except:
                return self.master.current_total_count
        
        # 仅在需要时强制刷新
        current_count = self.master.current_total_count
        if force_refresh:
            current_count = _get_current_count()
            with self.lock:
                self.master.current_total_count = current_count
        
        # 计算时间差（使用高精度计时器）
        duration = (datetime.now() - self.start_time).total_seconds()
        if duration < 0.5:
            return 0

        # 计算速度（线程安全）
        with self.lock:
            speed = (current_count - self.start_count) / duration * 3600
            return max(speed, 0)  # 确保非负

    def stop(self):
        if not self.active:
            return 0
        self.active = False
        with safe_file_access(JSON_FILE, 'r') as f:
            end_count = json.load(f)['total']
        duration = (datetime.now() - self.start_time).total_seconds()
        return (end_count - self.start_count) / duration * 3600 if duration > 0 else 0


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        
        # 确保目录和文件存在
        os.makedirs(os.path.dirname(JSON_FILE), exist_ok=True)
        DataProcessor.process_data()
        if not os.path.exists(JSON_FILE):
            DataProcessor.process_data()

            
        self.title("字数统计工具 by hyuan")
        self.geometry("400x300")
        self.json_last_modified = 0

        # 启动时隐藏主窗口
        self.withdraw()

        # 初始化任务栏窗口
        self.taskbar = TaskbarWindow(self)

        # 初始化系统托盘
        self.tray = SysTrayManager(self)

        # 窗口关闭时隐藏
        self.protocol("WM_DELETE_WINDOW", self.hide_window)

        # 初始化UI
        self._init_ui()
        self._start_background_tasks()

        self.current_total_count = 0
        self.data_lock = threading.Lock()

        # 回调注册
        DataProcessor.set_update_callback(self.update_total_count)
                


    def update_total_count(self, total):
        with self.data_lock:  # 线程安全更新
            self.current_total_count = total

    def _init_ui(self):
        # 统计数据显示标签
        self.lbl_day = ttk.Label(self, text="当日字数：加载中...")
        self.lbl_month = ttk.Label(self, text="本月字数：加载中...")
        self.lbl_year = ttk.Label(self, text="本年字数：加载中...")
        self.lbl_total = ttk.Label(self, text="总字数：加载中...")

        # 测速组件
        self.speed_tester = SpeedTester(self)
        self.lbl_speed = ttk.Label(self, text="输入速度：未测速")
        self.btn_speed = ttk.Button(self, text="开始测速", command=self.toggle_speed_test)

        # 历史记录按钮
        self.btn_history = ttk.Button(self, text="历史记录", command=self.show_history)

        # 布局组件
        components = [self.lbl_day, self.lbl_month, self.lbl_year,
                     self.lbl_total, self.lbl_speed, self.btn_speed, self.btn_history]
        for comp in components:
            comp.pack(pady=5)

    def _start_background_tasks(self):
        # 文件监控
        self.observer = Observer()
        self.observer.schedule(CSVHandler(), os.path.dirname(CSV_FILE), recursive=False)
        self.observer.start()

        # 定时任务自动清理csv文档
        # 每日凌晨00:00执行清空csv文档的操作，保障csv文档的轻量，可以按需调整清理时间在every().day括号里填你想要的天数就好。这里16:00是UTC时间，对应北京时间就是00:00。
        schedule.every().day.at("16:00").do(self._clear_csv)
        # schedule.every(1).minutes.do(self._clear_csv) #测试用的，一分钟测试一下有没有清空

        # 调度线程
        def scheduler_loop():
            while True:
                schedule.run_pending()
                time.sleep(3600)
        threading.Thread(target=scheduler_loop, daemon=True).start()

        # 启动数据更新循环
        self.after(1000, self.update_display)

    def update_display(self):
        """更新GUI显示（3秒刷新一次）"""
        try:
            current_modified = os.path.getmtime(JSON_FILE)
            if current_modified > self.json_last_modified:
                self.json_last_modified = current_modified
                threading.Thread(target=self._update_stats_display, daemon=True).start()

            if hasattr(self, 'speed_tester') and self.speed_tester.active:
                threading.Thread(target=self._update_speed_thread, daemon=True).start()

        except Exception as e:
            print(f"更新显示失败: {e}")
        finally:
            self.after(3000, self.update_display) 

    def _update_stats_display(self):
        """异步更新统计数据"""
        try:
            with safe_file_access(JSON_FILE, 'r') as f:
                stats = json.load(f)
                now = datetime.now()
                self.after(0, lambda: [
                    self.lbl_day.config(text=f"当日字数：{stats['daily'].get(now.strftime('%Y-%m-%d'), 0)}"),
                    self.lbl_month.config(text=f"本月字数：{stats['monthly'].get(now.strftime('%Y-%m'), 0)}"),
                    self.lbl_year.config(text=f"本年字数：{stats['yearly'].get(now.strftime('%Y'), 0)}"),
                    self.lbl_total.config(text=f"总字数：{stats['total']}")
                ])
                # 更新缓存
                self.current_total_count = stats['total']
        except Exception as e:
            print(f"更新统计数据失败: {e}")

    def _update_speed_thread(self):
        """异步更新速度显示"""
        try:
            # 在独立线程中计算速度（关键修改）
            def _async_speed_update():
                if getattr(self, 'speed_refresh_counter', 0) % 5 == 0:
                    speed = self.speed_tester.get_speed(force_refresh=True)
                else:
                    speed = self.speed_tester.get_speed()
                
                self.after(0, lambda: self._update_speed_display(speed))
            
            threading.Thread(target=_async_speed_update, daemon=True).start()
            self.speed_refresh_counter = getattr(self, 'speed_refresh_counter', 0) + 1
            
        except Exception as e:
            print(f"更新速度失败: {e}")

    def _update_speed_display(self, speed):
        """更新速度显示样式（修改颜色逻辑）"""
        # 判断当前是否处于测速状态
        if self.speed_tester.active:
            # 根据速度值设置颜色
            color = '#0000FF'  # 默认蓝色
            if speed >= 800:
                color = '#00FF00' if speed <= 1500 else '#FFA500'  # 绿色/橙色
        else:
            color = 'black'  # 测速结束后恢复黑色
        self.lbl_speed.config(text=f"输入速度：{speed:.1f}字/小时", foreground=color)

    def toggle_speed_test(self):
        """切换测速状态"""
        if self.btn_speed['text'] == '开始测速':
            self.speed_tester.start()
            self.btn_speed.config(text='结束测速')
            self.lbl_speed.config(text="测速中...")
        else:
            final_speed = self.speed_tester.stop()
            self.btn_speed.config(text='开始测速')
            self._update_speed_display(final_speed)

    def show_history(self):
        """显示历史记录窗口"""
        HistoryWindow(self)

    def hide_window(self):
        """隐藏主窗口"""
        self.withdraw()

    def _clear_csv(self):
        """清空CSV文件"""
        try:
            with safe_file_access(CSV_FILE, 'w') as f:
                f.write("timestamp,chinese_count\n")
            with safe_file_access(JSON_FILE, 'r+') as f:
                stats = json.load(f)
                stats[CURRENT_SYSTEM] = 0
                f.seek(0)
                json.dump(stats, f, indent=2, ensure_ascii=False)
                f.truncate()
        except Exception as e:
            print(f"清空失败: {e}")


# ================== 历史记录窗口 ==================
class HistoryWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("历史记录")
        self.geometry("500x400")
        self.year_data = defaultdict(dict)  # 数据结构：{年份: {"total": 总字数, "months": {月份: 字数}}}
        self._load_data()
        self._create_widgets()

    def _create_widgets(self):
        # 创建年度选项卡
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True)
        
        # 为每个年份创建标签页
        for year in sorted(self.year_data.keys(), reverse=True):
            frame = ttk.Frame(self.notebook)
            self._create_year_tab(frame, year)
            self.notebook.add(frame, text=f"{year}年")

    def _create_year_tab(self, frame, year):
        # 创建年度统计表格（调整后的列结构）
        columns = ('时间段', '字数', '详情')
        tree = ttk.Treeview(frame, columns=columns, show='headings', height=15)
        
        # 设置列宽
        tree.column('时间段', width=100, anchor='w')
        tree.column('字数', width=70, anchor='center')
        tree.column('详情', width=50, anchor='center')
        
        # 设置表头
        tree.heading('时间段', text='时间段')
        tree.heading('字数', text='字数统计')
        tree.heading('详情', text='每日详情')
        
        # 插入年度总字数行
        year_total = self.year_data[year]['total']
        tree.insert('', 'end', values=(f"【{year}年总字数】", year_total, ''), tags=('total',))
        
        # 插入月份数据
        for month in sorted(self.year_data[year]['months'].keys(), reverse=True):
            month_count = self.year_data[year]['months'][month]
            tree.insert('', 'end', values=(month, month_count, '查看详情'), tags=(month,))
        
        # 添加按钮绑定（仅绑定月份行）
        tree.tag_bind(month, '<Button-1>', lambda e: self._show_daily_detail(e))
        tree.pack(fill='both', expand=True, padx=10, pady=10)

        # 设置年度总字数行样式
        tree.tag_configure('total', background='#F0F0F0', font=('微软雅黑', 10, 'bold'))

    def _load_data(self):
        try:
            with open(JSON_FILE, 'r', encoding='utf-8') as f:
                stats = json.load(f)
            
            # 重组数据结构
            for month_key, count in stats['monthly'].items():
                year = month_key.split('-')[0]
                # 初始化年份数据结构
                if year not in self.year_data:
                    self.year_data[year] = {'total': 0, 'months': {}}
                # 累加年度总字数
                self.year_data[year]['total'] += count
                # 存储月份数据
                self.year_data[year]['months'][month_key] = count
                
        except Exception as e:
            messagebox.showerror("错误", f"加载历史数据失败: {e}")

    def _show_daily_detail(self, event):
    # 获取选中月份
        tree = event.widget
        item = tree.identify_row(event.y)
        month = tree.item(item, 'values')[0]  # 索引0对应月份
        
        # 创建详情窗口并设置尺寸
        detail_win = tk.Toplevel(self)
        detail_win.title(f"{month}每日统计")
        detail_win.geometry("400x300")  # 新增窗口尺寸设置
        
        # 创建带滚动条的容器
        container = ttk.Frame(detail_win)
        container.pack(fill='both', expand=True, padx=10, pady=10)
        
        # 创建表格（添加滚动条优化）
        columns = ('日期', '当日字数')
        tree = ttk.Treeview(
            container, 
            columns=columns, 
            show='headings',
            selectmode='extended'
        )
        
        # 添加垂直滚动条
        vsb = ttk.Scrollbar(
            container, 
            orient="vertical", 
            command=tree.yview
        )
        tree.configure(yscrollcommand=vsb.set)
        
        # 布局表格和滚动条
        tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        
        # 设置容器网格行列权重
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        
        # 设置表格列宽
        tree.column('日期', width=200, anchor='w')
        tree.column('当日字数', width=150, anchor='center')
        
        # 设置表头
        tree.heading('日期', text='日期')
        tree.heading('当日字数', text='字数')
        
        # 添加数据
        try:
            with open(JSON_FILE, 'r', encoding='utf-8') as f:
                stats = json.load(f)
            
            daily_data = {k:v for k,v in stats['daily'].items() if k.startswith(month)}
            for date in sorted(daily_data.keys(), reverse=True):
                tree.insert('', 'end', values=(date, daily_data[date]))
                
        except Exception as e:
            messagebox.showerror("错误", f"加载每日数据失败: {e}")

        # 添加自适应布局
        detail_win.minsize(600, 400)  # 设置最小窗口尺寸

if __name__ == "__main__":
    gui = Application()
    gui.mainloop()
