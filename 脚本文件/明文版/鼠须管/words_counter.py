"""

版本: 字数统计工具-鼠须管明文版-1.0
作者: hyuan
Github:https://github.com/hyuan42/Rime-words-counter

脚本功能：处理LUA脚本记录的字数+明文数据，显示主页面UI，在后台静默运行。
介意隐私问题，不想明文记载哪个时间段打了哪些字的，请用字数版。

使用前，记得安装以下依赖库：
pip install rumps portalocker watchdog schedule

"""


import os
import sys
import json
import csv
import time
import threading
import portalocker  
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import schedule
import tkinter as tk
from tkinter import ttk, messagebox

# ========== 统一路径配置 ==========
CUSTOM_PATH = "/Users/iCloud/Example"  #字数统计数据的json文档存放的文件夹的路径。想要多设备同步数据的，把这个json文件放到同步网盘好一点
CUSTOM_PATH2 = "/Users/你的设备名/Library/Rime/py_wordscounter"  #放置Python脚本和读取csv文档的路径，跟lua脚本里的路径要一致
# 确保目录存在
if not os.path.exists(CUSTOM_PATH):
    os.makedirs(CUSTOM_PATH)

# 定义文件路径
JSON_FILE = os.path.join(CUSTOM_PATH, "words_count_history.json")  #核心文档。记录每天/月/年/总输入字数的文档
CSV_FILE = os.path.join(CUSTOM_PATH2, "words_input.csv")  #记录通过words_counter.lua记录下来的字数、上屏文本的文档。Python脚本通过这个文档统计今日总字数并更新到json中
SIGNAL_FILE = os.path.join(CUSTOM_PATH2, ".show_gui_signal")  #信号文件。用来和status_bar_app.py通信的，把今日字数显示在macOS顶部状态栏(menus bar)上

#多设备时，每个设备命名要不一样，而且记得打开words_count_history.json手动修改，有多少个设备，就添加多少行last_processed_row_其他设备名。
#用处: 记录本设备的csv文档里最后一行的行数到json中，每次打字时，csv文档有新增的行数，Python脚本就会从记录的行数开始算新增行数的字数们，达到增量统计，而不是每次都需要从第一行算到最后一行。
CURRENT_SYSTEM = "last_processed_row_mac"


# ========== 安全文件访问函数 ==========
def safe_file_access(file_path, mode, retries=3, delay=0.1):
    """跨平台安全文件访问"""
    for attempt in range(retries):
        try:
            f = open(file_path, mode, encoding='utf-8')
            portalocker.lock(f, portalocker.LOCK_EX)
            return f
        except (IOError, portalocker.LockException) as e:
            if attempt == retries - 1:
                raise
            time.sleep(delay)
    raise Exception(f"文件访问失败: {file_path}")


# ========== 数据处理类 ==========
# 读取csv文档里的数据，汇总到json文档中
class DataProcessor:
    @staticmethod
    def process_data():
        """带跨平台锁的数据处理"""
        try:
            with safe_file_access(JSON_FILE, 'r+') as f:
                stats = {"daily": {}, "monthly": {}, "yearly": {}, "total": 0, "last_processed_row_mac": 0}
                
                if os.path.getsize(JSON_FILE) > 0:
                    stats.update(json.load(f))
                
                new_data = []
                if os.path.exists(CSV_FILE):
                    with safe_file_access(CSV_FILE, 'r') as csv_f:
                        reader = csv.reader(csv_f)
                        try:
                            header = next(reader)
                            if header != ['timestamp', 'chinese_count', 'text']:
                                raise ValueError("CSV文件头格式错误")
                        except StopIteration:
                            return
                        
                        actual_lines = sum(1 for _ in reader)
                        csv_f.seek(0)
                        next(reader)
                        
                        start_row = min(stats[CURRENT_SYSTEM], actual_lines)
                        for _ in range(start_row):
                            next(reader, None)
                        
                        for row in reader:
                            if len(row) != 3:
                                continue
                            try:
                                new_data.append({
                                    "timestamp": row[0],
                                    "chinese_count": int(row[1])
                                })
                            except ValueError:
                                continue
                        
                        stats[CURRENT_SYSTEM] = actual_lines
                
                for row in new_data:
                    timestamp = datetime.fromisoformat(row['timestamp'])
                    count = row['chinese_count']
                    
                    day_key = timestamp.strftime('%Y-%m-%d')
                    stats["daily"][day_key] = stats["daily"].get(day_key, 0) + count
                    
                    month_key = timestamp.strftime('%Y-%m')
                    stats["monthly"][month_key] = stats["monthly"].get(month_key, 0) + count
                    
                    year_key = timestamp.strftime('%Y')
                    stats["yearly"][year_key] = stats["yearly"].get(year_key, 0) + count
                    
                    stats["total"] += count
                
                f.seek(0)
                json.dump(stats, f, indent=2)
                f.truncate()
        except Exception as e:
            print(f"数据处理错误: {str(e)}")


# ========== 文件监控类 ==========
# 监控csv文件，当csv文件有修改（也就是有打字时会新增行数），才会处理/更新数据到json
class CSVHandler(FileSystemEventHandler):
    def __init__(self):
        self.last_processed = 0
    
    def on_modified(self, event):
        if event.src_path == CSV_FILE and time.time() - self.last_processed > 0.5:
            self.last_processed = time.time()
            DataProcessor.process_data()
            if gui:
                gui.update_display()

# 监控json文件，当json文件有变动（有新增字数），才会处理/更新数据到前端GUI界面
class JSONUpdateHandler(FileSystemEventHandler):
    def __init__(self, app):
        self.app = app
        self.last_trigger = 0

    def on_modified(self, event):
        if event.src_path == JSON_FILE and time.time() - self.last_trigger > 0.5:
            self.last_trigger = time.time()
            self.app.update_display()  

# ========== 测速功能类 ==========
# 整体实现逻辑是：点击测速后，记录csv文档里的当前行数，通过新增行数的时间戳计算时间，以及计算新增的字数。（新增字数➗时间）*3600=当前速度/小时
class SpeedTester:
    def __init__(self):
        self.start_time = None
        self.start_row = 0
        self.last_speed = "未测速"
        self.active = False
        self.lock = threading.Lock()

    def start_test(self):
        with self.lock:
            self.start_time = datetime.now()
            self.start_row = self._get_current_row_count()
            self.active = True

    def stop_test(self):
        with self.lock:
            if not self.active:
                return 0
            
            end_row = self._get_current_row_count()
            total_chars = self._get_chars_between_rows(self.start_row, end_row)
            duration = (datetime.now() - self.start_time).total_seconds()
            
            speed = total_chars / duration * 3600 if duration > 0 else 0
            self.last_speed = f"{speed:.1f}字/小时"
            self.active = False
            return speed

    def get_current_speed(self):
        with self.lock:
            if not self.active:
                return 0
            
            current_row = self._get_current_row_count()
            total_chars = self._get_chars_between_rows(self.start_row, current_row)
            duration = (datetime.now() - self.start_time).total_seconds()
            return total_chars / duration * 3600 if duration > 0 else 0

    def _get_current_row_count(self):
        try:
            if not os.path.exists(CSV_FILE):
                return 0
            with safe_file_access(CSV_FILE, 'r') as f:
                return sum(1 for _ in csv.reader(f)) - 1
        except Exception as e:
            print(f"行数统计错误: {e}")
            return 0

    def _get_chars_between_rows(self, start, end):
        if start >= end:
            return 0
        
        total = 0
        try:
            with safe_file_access(CSV_FILE, 'r') as f:
                reader = csv.reader(f)
                next(reader)
                
                for _ in range(start):
                    next(reader, None)
                
                for _ in range(end - start):
                    row = next(reader, None)
                    if row and len(row) >= 2:
                        try:
                            total += int(row[1])
                        except ValueError:
                            continue
        except Exception as e:
            print(f"字数统计错误: {e}")
        
        return total


# ========== 历史记录窗口 ==========
class HistoryWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("历史记录")
        self.geometry("800x500")
        self.style = ttk.Style()
        self.style.configure("Treeview.Heading", font=('苹方', 12))
        self.style.configure("Treeview", font=('苹方', 11), rowheight=25)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(expand=True, fill='both')
        self.load_data()

    def load_data(self):
        try:
            with safe_file_access(JSON_FILE, 'r') as f:
                stats = json.load(f)
            
            yearly_data = {}
            year_totals = {k: v for k, v in stats['yearly'].items()}

            for month_key in sorted(stats['monthly'], reverse=True):
                year = month_key.split('-')[0]
                yearly_data.setdefault(year, {}).setdefault('months', []).append(
                    (month_key, stats['monthly'][month_key]))
                yearly_data[year]['total'] = year_totals.get(year, 0)

            for year in sorted(yearly_data.keys(), reverse=True):
                frame = ttk.Frame(self.notebook)
                self.notebook.add(frame, text=f"{year}年度")

                tree = ttk.Treeview(
                    frame,
                    columns=('period', 'count', 'action'),
                    show='headings',
                    style='Treeview'
                )
                tree.heading('period', text='期间')
                tree.heading('count', text='字数')
                tree.heading('action', text='操作')
                tree.column('period', width=200, anchor='center')
                tree.column('count', width=150, anchor='center')
                tree.column('action', width=100, anchor='center')

                tree.insert('', 'end', 
                          values=(f"{year}年度总计", 
                                  f"{yearly_data[year]['total']:,}", ""),
                          tags=('year_total',))
                
                for month_key, count in yearly_data[year]['months']:
                    tree.insert('', 'end', 
                              values=(month_key, f"{count:,}", "查看详情"))

                tree.tag_configure('year_total', background='#4F637D', font=('苹方', 11, 'bold'))
                tree.bind('<ButtonRelease-1>', self.on_tree_click)
                tree.pack(expand=True, fill='both', padx=10, pady=10)

        except Exception as e:
            messagebox.showerror("错误", f"加载数据失败: {e}")

    def on_tree_click(self, event):
        tree = event.widget
        item = tree.identify_row(event.y)
        column = tree.identify_column(event.x)

        if 'year_total' in tree.item(item, 'tags'):
            return

        if item and column == '#3':
            month_key = tree.item(item, 'values')[0]
            self.show_daily_window(month_key)

    def show_daily_window(self, month_key):
        DailyWindow(self, month_key).grab_set()

class DailyWindow(tk.Toplevel):
    def __init__(self, parent, month_key):
        super().__init__(parent)
        self.title(f"{month_key} 每日统计")
        self.geometry("600x400")

        self.tree = ttk.Treeview(
            self,
            columns=('date', 'count'),
            show='headings',
            style='Center.Treeview'
        )
        self.tree.heading('date', text='日期')
        self.tree.heading('count', text='字数')
        self.tree.column('date', width=250, anchor='center')
        self.tree.column('count', width=250, anchor='center')

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.load_data(month_key)

    def load_data(self, month_key):
        try:
            with open(JSON_FILE, 'r', encoding='utf-8') as f:
                stats = json.load(f)

            daily_data = [
                (date, count)
                for date, count in stats['daily'].items()
                if date.startswith(month_key)
            ]

            for date, count in sorted(daily_data, reverse=True):
                self.tree.insert('', 'end', values=(date, f"{count:,}"))

        except Exception as e:
            messagebox.showerror("错误", f"加载每日数据失败: {e}")


# ========== GUI主程序 ==========
class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("字数统计工具 by hyuan")
        self.geometry("400x300")
        self.signal_file = SIGNAL_FILE
        self.withdraw()
        self.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.create_widgets()
        self.speed_tester = SpeedTester()
        DataProcessor.process_data()
        self.update_display()
        self.start_signal_checker()
        self.start_file_monitor()
        self.start_scheduler()
        self.create_exit_handler()
        self.json_observer = Observer()
        self.json_observer.schedule(
            JSONUpdateHandler(self),
            path=os.path.dirname(JSON_FILE),
            recursive=False
        )
        self.json_observer.start()


    def create_exit_handler(self):
        self.bind_all('<Control-q>', self.full_exit)
        self.createcommand('exit', self.full_exit)       

    def hide_window(self):
        self.withdraw()
        if os.path.exists(self.signal_file):
            os.remove(self.signal_file)

    def show_window(self):
        self.deiconify()
        self.lift()
        self.attributes('-topmost', 1)
        self.after(100, lambda: self.attributes('-topmost', 0))

    def start_signal_checker(self):
        def checker():
            while True:
                if os.path.exists(self.signal_file):
                    self.show_window()
                    try:
                        os.remove(self.signal_file)
                    except:
                        pass
                time.sleep(0.3)
        threading.Thread(target=checker, daemon=True).start()

    def create_widgets(self):
        self.labels = {
            'daily': tk.Label(self, text="当日字数：加载中...", font=('微软雅黑', 12)),
            'monthly': tk.Label(self, text="本月字数：加载中...", font=('微软雅黑', 12)),
            'yearly': tk.Label(self, text="本年字数：加载中...", font=('微软雅黑', 12)),
            'total': tk.Label(self, text="总字数：加载中...", font=('微软雅黑', 12)),
            'speed': tk.Label(self, text="输入速度：未测速", font=('微软雅黑', 12))
        }
        for label in self.labels.values():
            label.pack(pady=5)

        self.speed_button = tk.Button(
            self,
            text="开始测速",
            command=self.toggle_speed_test,
            font=('微软雅黑', 10),
            width=10
        )
        self.speed_button.pack(pady=8)

        self.history_button = tk.Button(
            self,
            text="历史记录",
            command=self.show_history,
            font=('微软雅黑', 10),
            width=10
        )
        self.history_button.pack(pady=8)

    def update_display(self):
        try:
            with safe_file_access(JSON_FILE, 'r') as f:
                stats = json.load(f)
            
            now = datetime.now()
            daily = stats['daily'].get(now.strftime('%Y-%m-%d'), 0)
            monthly = stats['monthly'].get(now.strftime('%Y-%m'), 0)
            yearly = stats['yearly'].get(now.strftime('%Y'), 0)

            self.labels['daily'].config(text=f"当日字数：{daily}")
            self.labels['monthly'].config(text=f"本月字数：{monthly}")
            self.labels['yearly'].config(text=f"本年字数：{yearly}")
            self.labels['total'].config(text=f"总字数：{stats['total']}")

            if not self.speed_tester.active:
                self.labels['speed'].config(
                    text=f"输入速度：{self.speed_tester.last_speed}"
                )
        except Exception as e:
            messagebox.showerror("错误", f"更新显示失败: {e}")

    def toggle_speed_test(self):
        if self.speed_button['text'] == '开始测速':
            self.start_speed_test()
        else:
            self.stop_speed_test()

    def start_speed_test(self):
        self.speed_tester.start_test()
        self.speed_button.config(text='结束测速', bg='#FF6666')
        self.labels['speed'].config(text="输入速度：测速中...", fg='blue')
        self.update_speed_display()
        self.speed_update_id = self.after(2000, self.update_speed_display)

    def stop_speed_test(self):
        speed = self.speed_tester.stop_test()
        self.labels['speed'].config(
            text=f"输入速度：{self.speed_tester.last_speed}",
            fg = 'SystemButtonText'
        )
        self.speed_button.config(text='开始测速', bg='SystemButtonFace')

    def update_speed_display(self):
        if self.speed_tester.active:
            speed = self.speed_tester.get_current_speed()
            self._update_speed_label(speed)
            self.after(2000, self.update_speed_display)

    def show_final_speed(self, speed):
        self._update_speed_label(speed)
        self.speed_tester.last_speed = f"{speed:.1f}字/小时"

    def _update_speed_label(self, speed):
        """在测速过程中根据输入速度更新颜色，想改什么颜色都可以。想多加几个层级也可以，看你喜欢"""
        if self.speed_tester.active:
            color = '#0066CC'
            if 800 <= speed <= 1500:
                color = '#009933'
            elif speed > 1500:
                color = '#FF6600'
            self.labels['speed'].config(
                text=f"输入速度：{speed:.1f}字/小时",
                fg=color
            )

    def show_history(self):
        HistoryWindow(self)

    def start_file_monitor(self):
        self.observer = Observer()
        self.observer.schedule(CSVHandler(), path=os.path.dirname(CSV_FILE), recursive=False)
        self.observer.start()

    def start_scheduler(self):
        def clear_csv():
            try:
                # 确保在清理前处理完所有数据
                DataProcessor.process_data()
                
                # 使用文件锁安全清空CSV
                with safe_file_access(CSV_FILE, 'w') as f:
                    f.write("timestamp,chinese_count,text\n")
                
                # 更新JSON中的行号记录
                with safe_file_access(JSON_FILE, 'r+') as f:
                    stats = json.load(f)
                    stats[CURRENT_SYSTEM] = 0
                    f.seek(0)
                    json.dump(stats, f, indent=2)
                    f.truncate()
                
                self.update_display()
                print(f"[{datetime.now()}] CSV文件已清理") 
            except Exception as e:
                print(f"清理失败: {e}")

        # 自动清理csv文档
        # 每日凌晨00:00执行清空csv文档的操作，保障csv文档的轻量，可以按需调整清理时间在every().day括号里填你想要的天数就好。
        schedule.every().day.at("00:00").do(clear_csv)
        # 测试用，1分钟清理一次
        # schedule.every(1).minutes.do(clear_csv)
        
        # 启动独立调度线程
        def scheduler_loop():
            while True:
                schedule.run_pending()
                time.sleep(3600)  
        
        threading.Thread(target=scheduler_loop, daemon=True).start()

    def _run_scheduler(self):
        while True:
            schedule.run_pending()
            time.sleep(1)

        threading.Thread(target=run_scheduler, daemon=True).start()


    def on_closing(self):
        self.observer.stop()
        self.observer.join()
        self.destroy()

    @staticmethod
    def clear_csv():
        """安全清空CSV文件（可被外部调用）"""
        try:
            DataProcessor.process_data()  # 确保处理所有数据
            with safe_file_access(CSV_FILE, 'w') as f:
                f.write("timestamp,chinese_count,text\n")
            
            # 更新JSON中的行号记录
            with safe_file_access(JSON_FILE, 'r+') as f:
                stats = json.load(f)
                stats[CURRENT_SYSTEM] = 0
                f.seek(0)
                json.dump(stats, f, indent=2)
                f.truncate()
            return True
        except Exception as e:
            print(f"清理失败: {e}")
            return False

    def full_exit(self, event=None):
        """安全退出时停止所有监听"""
        if hasattr(self, 'json_observer'):
            self.json_observer.stop()
            self.json_observer.join(timeout=1)

    def full_exit(self, event=None):
        """完全退出时的资源清理"""
        print("[System] 正在执行安全退出流程...")
        try:
            # 停止文件监控
            if hasattr(self, 'observer'):
                self.observer.stop()
                self.observer.join(timeout=1)
            
            # 释放所有文件锁
            if hasattr(self, '_file_handles'):
                for f in self._file_handles:
                    try:
                        portalocker.unlock(f)
                        f.close()
                    except Exception as e:
                        print(f"文件解锁失败: {e}")
            
            # macOS需要额外终止关联进程
            if sys.platform == 'darwin':
                subprocess.run(
                    ['pkill', '-f', os.path.basename(__file__)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
        finally:
            self.destroy()  # 彻底销毁窗口
            sys.exit(0)     # 确保进程终止


if __name__ == "__main__":
    if "--clear-csv" in sys.argv:
        result = Application.clear_csv()
        sys.exit(0 if result else 1)
    elif "--full-exit" in sys.argv:  # 新增退出指令
        gui = Application()
        gui.full_exit()
    else:
        gui = Application()
        gui.mainloop()
