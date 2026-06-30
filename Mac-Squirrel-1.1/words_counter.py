"""

版本: 字数统计工具-鼠须管-v1.1
作者: hyuan
Github仓库: https://github.com/hyuan42/Rime-words-counter
时间: 2026-06-26

脚本功能：处理LUA脚本记录的字数数据（不记录明文，只记录上屏的汉字个数），统计汇总到json文件，并在系统托盘创建图标、创建一个悬浮窗口实时展示今日字数，同时还具有测速功能、查看历史统计字数的功能等。

运行脚本需要安装 Python 环境，使用前安装以下依赖库：
pip install rumps portalocker watchdog

"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

from watchdog.events import PatternMatchingEventHandler
from watchdog.observers import Observer

# wc_core 与本文件在同一目录
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
from wc_core import (  # noqa: E402
    DataProcessor, app_support_dir, bus, load_config, rename_device, safe_file, schedule_daily,
)

# ========== 加载配置 ==========
config = load_config()
CSV_FILE = str(config.csv_path)
JSON_FILE = str(config.json_path)
SIGNAL_FILE = str(app_support_dir() / ".show_gui_signal")
DEVICE_ID = config.device_id

processor = DataProcessor(CSV_FILE, JSON_FILE, DEVICE_ID)


# ========== 防抖动 debounce ==========
class Debouncer:
    """trailing debounce：连续触发只在静默 delay 后执行一次。"""

    def __init__(self, delay: float, callback):
        self.delay = delay
        self.callback = callback
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def trigger(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self):
        try:
            self.callback()
        except Exception as e:
            print(f"[debounce] {e}")


# ========== 文件监控 ==========
class CSVHandler(PatternMatchingEventHandler):
    """只关心 CSV_FILE 的修改事件。"""

    def __init__(self, on_change):
        super().__init__(patterns=[CSV_FILE], ignore_directories=True)
        self._debounce = Debouncer(0.5, on_change)

    def on_modified(self, event):
        self._debounce.trigger()

    def on_created(self, event):
        self._debounce.trigger()


# ========== 测速 ==========
class SpeedTester:
    """口径：以本设备 CSV 的增量字数为准（不依赖 total，避免多设备数据混入）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.active = False
        self.start_time: datetime | None = None
        self.start_chars = 0
        self.last_speed_label = "未测速"

    def _current_chars(self) -> int:
        if not os.path.exists(CSV_FILE):
            return 0
        total = 0
        try:
            with safe_file(CSV_FILE, "r") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if header is None:
                    return 0
                for row in reader:
                    if len(row) < 2:
                        continue
                    try:
                        total += int(row[1])
                    except ValueError:
                        continue
        except IOError as e:
            print(f"[speed] 读取 CSV 失败: {e}")
        return total

    def start(self):
        with self._lock:
            self.active = True
            self.start_time = datetime.now()
            self.start_chars = self._current_chars()

    def current_speed(self) -> float:
        with self._lock:
            if not self.active or self.start_time is None:
                return 0.0
            duration = (datetime.now() - self.start_time).total_seconds()
            if duration < 1.0:
                return 0.0
            chars = self._current_chars() - self.start_chars
            return max(0.0, chars / duration * 3600)

    def stop(self) -> float:
        with self._lock:
            if not self.active or self.start_time is None:
                self.active = False
                return 0.0
            duration = (datetime.now() - self.start_time).total_seconds()
            chars = self._current_chars() - self.start_chars
            speed = max(0.0, chars / duration * 3600) if duration > 0 else 0.0
            self.last_speed_label = f"{speed:.1f}字/小时"
            self.active = False
            return speed


# ========== 主题 ==========
class Theme:
    """深色卡片风格的主题色板与排版常量。改这里就能整体换色。"""

    BG = "#1B1D23"          # 主背景
    SURFACE = "#23262F"     # 卡片背景
    SURFACE_HI = "#2E323D"  # hover / 强调卡片
    BORDER = "#363B47"
    TEXT = "#ECEDEE"
    TEXT_DIM = "#8C92A3"
    ACCENT = "#7C9CFF"      # 主强调色（数字、按钮）
    ACCENT_HI = "#A4BCFF"
    DANGER = "#FF6B6B"
    SUCCESS = "#4ADE80"
    WARN = "#FFB84D"

    # 热力图 5 级（无数据 → 极多）
    # 热力图色阶（固定字数阈值）：
    #   0       → 默认面板灰色
    #   1-1999  → 极深蓝
    #   2000-3999 → 深蓝
    #   4000-5999 → 中蓝
    #   6000-9999 → 亮蓝
    #   10000+  → 紫色（爆字）
    HEAT_SCALE = ["#23262F", "#172E4E", "#173D70", "#1558A8", "#2488FF", "#7B24FF"]
    HEAT_THRESHOLDS = [0, 1999, 3999, 5999, 9999]   # <= 这些边界对应上面 1..5 档

    FONT_TITLE = ("PingFang SC", 18, "bold")
    FONT_LABEL = ("PingFang SC", 11)
    FONT_NUMBER = ("PingFang SC", 28, "bold")
    FONT_SMALL = ("PingFang SC", 10)


# ========== 历史窗口 ==========
def _enumerate_month_days(month_key: str) -> list[str]:
    """返回该月份的所有日期字符串，按新→旧排序。

    当月：从今天回溯到月初；过去月份：整月；未来月份：空列表。
    """
    try:
        year_str, month_str = month_key.split("-")
        year = int(year_str)
        month = int(month_str)
    except (ValueError, AttributeError):
        return []

    today = date.today()
    if (year, month) > (today.year, today.month):
        return []

    first = date(year, month, 1)
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    last = next_first - timedelta(days=1)
    if (year, month) == (today.year, today.month):
        last = today

    days: list[str] = []
    cur = last
    while cur >= first:
        days.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)
    return days


class HistoryWindow(tk.Toplevel):
    """历史记录窗口：年度统计（热力图）+ 月度统计（日历视图）两个 tab。"""

    # 热力图参数
    CELL = 13
    GAP = 3
    LEFT_PAD = 48     # 留出空间显示一二三四五六日
    TOP_PAD = 32

    def __init__(self, parent):
        super().__init__(parent)
        self.title("历史记录")
        self.configure(bg=Theme.BG)

        canvas_w = self.LEFT_PAD + 54 * (self.CELL + self.GAP) + 24
        win_w = canvas_w + 48
        self.geometry(f"{win_w}x642")
        self.minsize(win_w, 580)

        self._tooltip: tk.Toplevel | None = None
        self._tooltip_label: tk.Label | None = None
        self._cell_lookup: dict[int, tuple[str, int]] = {}

        # 共享状态
        self._all_years = self._discover_years()
        if not self._all_years:
            self._all_years = [date.today().year]

        today = date.today()
        self._year = today.year if today.year in self._all_years else max(self._all_years)
        self._month_key = today.strftime("%Y-%m")

        # 构建两个 tab
        self._build_tabs()
        self._render_year(self._year)
        self._render_month(self._month_key)

    # ============ 通用工具 ============
    def _discover_years(self) -> list[int]:
        try:
            agg = processor.aggregate()
        except Exception:
            return []
        years = set()
        for d in agg["daily"]:
            try:
                years.add(int(d.split("-")[0]))
            except (ValueError, IndexError):
                continue
        for y in agg["yearly"]:
            try:
                years.add(int(y))
            except ValueError:
                continue
        return sorted(years)

    def _thresholds(self, counts: list[int]) -> tuple[int, int, int]:
        """已废弃：现在用 Theme.HEAT_THRESHOLDS 固定阈值，不再用四分位数。
        保留签名以免上游调用报错。"""
        return 0, 0, 0

    @staticmethod
    def _color_for(count: int, *_unused) -> str:
        """根据 Theme.HEAT_THRESHOLDS 把字数映射到 6 档颜色。"""
        if count <= 0:
            return Theme.HEAT_SCALE[0]
        for idx, threshold in enumerate(Theme.HEAT_THRESHOLDS[1:], start=1):
            if count <= threshold:
                return Theme.HEAT_SCALE[idx]
        return Theme.HEAT_SCALE[5]  # >= 10000

    # ============ Tab 框架 ============
    def _build_tabs(self):
        # 顶部 tab 切换条
        tab_bar = tk.Frame(self, bg=Theme.BG)
        tab_bar.pack(fill="x", padx=24, pady=(20, 0))

        self._tab_year_btn = self._make_tab_button(tab_bar, "年度统计", lambda: self._switch_tab("year"))
        self._tab_year_btn.pack(side="left", padx=(0, 8))
        self._tab_month_btn = self._make_tab_button(tab_bar, "月度统计", lambda: self._switch_tab("month"))
        self._tab_month_btn.pack(side="left")

        # 两个 tab 内容容器（堆叠用 grid + raise/lower）
        self._tab_frames = {}
        wrap = tk.Frame(self, bg=Theme.BG)
        wrap.pack(fill="both", expand=True)
        self._tab_frames["year"] = tk.Frame(wrap, bg=Theme.BG)
        self._tab_frames["month"] = tk.Frame(wrap, bg=Theme.BG)
        for f in self._tab_frames.values():
            f.place(relwidth=1, relheight=1)

        self._build_year_tab(self._tab_frames["year"])
        self._build_month_tab(self._tab_frames["month"])

        self._current_tab = "year"
        self._switch_tab("year")

    def _make_tab_button(self, parent, text, cmd):
        frame = tk.Frame(parent, bg=Theme.SURFACE, bd=0, highlightthickness=0)
        label = tk.Label(
            frame, text=text, bg=Theme.SURFACE, fg=Theme.TEXT,
            font=("PingFang SC", 12, "bold"),
            cursor="hand2", padx=18, pady=8,
        )
        label.pack()
        frame._label = label
        for w in (frame, label):
            w.bind("<Button-1>", lambda _e: cmd())
        return frame

    def _switch_tab(self, name: str):
        # 高亮当前 tab
        for tab, btn in (("year", self._tab_year_btn), ("month", self._tab_month_btn)):
            color = Theme.ACCENT if tab == name else Theme.SURFACE
            fg = "#0F1218" if tab == name else Theme.TEXT
            btn.config(bg=color)
            btn._label.config(bg=color, fg=fg)
        self._current_tab = name
        self._tab_frames[name].tkraise()

    # ============ 年度 tab ============
    def _build_year_tab(self, parent):
        # 顶部栏：标题 + 左右切换
        bar = tk.Frame(parent, bg=Theme.BG)
        bar.pack(fill="x", padx=24, pady=(16, 8))
        self.lbl_year_title = tk.Label(
            bar, text="", bg=Theme.BG, fg=Theme.TEXT, font=Theme.FONT_TITLE
        )
        self.lbl_year_title.pack(side="left")
        nav = tk.Frame(bar, bg=Theme.BG)
        nav.pack(side="right")
        self.btn_prev_year = self._make_nav_button(nav, "‹", lambda: self._step_year(-1))
        self.btn_prev_year.pack(side="left", padx=(0, 6))
        self.btn_next_year = self._make_nav_button(nav, "›", lambda: self._step_year(1))
        self.btn_next_year.pack(side="left")

        # 热力图画布：精确高度 = TOP_PAD + 7 行方块 + 底部留白
        canvas_h = self.TOP_PAD + 7 * (self.CELL + self.GAP) + 16
        wrap = tk.Frame(parent, bg=Theme.BG)
        wrap.pack(fill="x", padx=24, pady=(0, 8))
        canvas_w = self.LEFT_PAD + 54 * (self.CELL + self.GAP) + 24
        self.canvas = tk.Canvas(
            wrap, bg=Theme.BG, highlightthickness=0, bd=0,
            width=canvas_w, height=canvas_h,
        )
        self.canvas.pack()
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Leave>", lambda _e: self._hide_tooltip())

        # 月度折线图 — 占满剩余垂直空间
        line_wrap = tk.Frame(parent, bg=Theme.BG)
        line_wrap.pack(fill="both", expand=True, padx=24, pady=(16, 0))
        tk.Label(
            line_wrap, text="月度趋势", bg=Theme.BG, fg=Theme.TEXT_DIM,
            font=Theme.FONT_LABEL,
        ).pack(anchor="w")
        self.line_canvas = tk.Canvas(
            line_wrap, bg=Theme.BG, highlightthickness=0, bd=0,
            width=canvas_w, height=260,
        )
        self.line_canvas.pack(fill="both", expand=True, pady=(6, 0))
        self.line_canvas.bind("<Motion>", self._on_line_motion)
        self.line_canvas.bind("<Leave>", lambda _e: self._hide_tooltip())
        # 点击节点 → 跳转到月度统计 tab
        self.line_canvas.bind("<Button-1>", self._on_line_click)
        # 窗口拉伸时重绘折线图
        self.line_canvas.bind("<Configure>", self._on_line_configure)
        # canvas_id -> (month_key, count) 用于 tooltip 和 click
        self._line_lookup: dict[int, tuple[str, int]] = {}

        # 摘要 + 图例
        summary = tk.Frame(parent, bg=Theme.BG)
        summary.pack(fill="x", padx=24, pady=(12, 20))
        self.lbl_year_summary = tk.Label(
            summary, text="", bg=Theme.BG, fg=Theme.TEXT_DIM, font=Theme.FONT_LABEL,
        )
        self.lbl_year_summary.pack(side="left")
        legend = tk.Frame(summary, bg=Theme.BG)
        legend.pack(side="right")
        tk.Label(legend, text="少 ", bg=Theme.BG, fg=Theme.TEXT_DIM,
                 font=Theme.FONT_SMALL).pack(side="left")
        for color in Theme.HEAT_SCALE:
            sw = tk.Frame(legend, bg=color, width=12, height=12)
            sw.pack(side="left", padx=1)
            sw.pack_propagate(False)
        tk.Label(legend, text=" 多", bg=Theme.BG, fg=Theme.TEXT_DIM,
                 font=Theme.FONT_SMALL).pack(side="left")

    def _make_nav_button(self, parent, text, cmd):
        """箭头按钮：用 Frame+Label 模拟（tk.Button 在 macOS 上 fg 会被忽略）。"""
        frame = tk.Frame(parent, bg=Theme.SURFACE, bd=0, highlightthickness=0)
        label = tk.Label(
            frame, text=text, bg=Theme.SURFACE, fg=Theme.TEXT,
            font=("PingFang SC", 16, "bold"),
            cursor="hand2", padx=12, pady=2,
        )
        label.pack()
        for w in (frame, label):
            w.bind("<Enter>", lambda _e: (frame.config(bg=Theme.SURFACE_HI),
                                            label.config(bg=Theme.SURFACE_HI)))
            w.bind("<Leave>", lambda _e: (frame.config(bg=Theme.SURFACE),
                                            label.config(bg=Theme.SURFACE)))
            w.bind("<Button-1>", lambda _e: cmd())
        return frame

    def _step_year(self, delta: int):
        years = sorted(set(self._all_years + [date.today().year]))
        if self._year not in years:
            return
        idx = years.index(self._year) + delta
        if 0 <= idx < len(years):
            self._year = years[idx]
            self._render_year(self._year)

    def _render_year(self, year: int):
        try:
            agg = processor.aggregate()
        except Exception as e:
            messagebox.showerror("错误", f"加载数据失败: {e}")
            return
        daily = agg["daily"]
        year_total = agg["yearly"].get(str(year), 0)
        active_days = sum(1 for d, c in daily.items()
                          if d.startswith(f"{year}-") and c > 0)
        self.lbl_year_title.config(text=f"{year} 年度  ·  {year_total:,} 字")
        self.lbl_year_summary.config(
            text=f"{active_days} 天有记录   ·   平均 {year_total // max(1, active_days):,} 字/活跃日"
        )

        counts = [c for d, c in daily.items() if d.startswith(f"{year}-") and c > 0]
        t1, t2, t3 = self._thresholds(counts)

        self.canvas.delete("all")
        self._cell_lookup.clear()

        jan1 = date(year, 1, 1)
        start = jan1 - timedelta(days=jan1.weekday())
        end = date(year, 12, 31)
        weeks = ((end - start).days // 7) + 2

        height = self.TOP_PAD + 7 * (self.CELL + self.GAP) + 16
        self.canvas.config(height=height)

        # 月份标注
        month_cols = {}
        for week in range(weeks):
            for offset in range(7):
                d = start + timedelta(weeks=week, days=offset)
                if d.year == year and d.month not in month_cols:
                    month_cols[d.month] = week
        for month, col in month_cols.items():
            x = self.LEFT_PAD + col * (self.CELL + self.GAP)
            self.canvas.create_text(
                x, self.TOP_PAD - 16, text=f"{month}月",
                anchor="w", fill=Theme.TEXT_DIM, font=Theme.FONT_SMALL,
            )

        # 周一到周日 全部显示
        weekday_labels = ["一", "二", "三", "四", "五", "六", "日"]
        for wd, label in enumerate(weekday_labels):
            y = self.TOP_PAD + wd * (self.CELL + self.GAP) + self.CELL // 2
            self.canvas.create_text(
                self.LEFT_PAD - 10, y, text=label, anchor="e",
                fill=Theme.TEXT_DIM, font=Theme.FONT_SMALL,
            )

        # 方块
        today = date.today()
        for week in range(weeks):
            for wd in range(7):
                d = start + timedelta(weeks=week, days=wd)
                if d.year != year:
                    continue
                x0 = self.LEFT_PAD + week * (self.CELL + self.GAP)
                y0 = self.TOP_PAD + wd * (self.CELL + self.GAP)
                key = d.strftime("%Y-%m-%d")

                if d > today:
                    # 未来日期：用 0 字数同色（灰），无数据
                    fill = Theme.HEAT_SCALE[0]
                    count = 0
                    payload = (key, None)   # tooltip 显示"未到"
                else:
                    count = daily.get(key, 0)
                    fill = self._color_for(count)
                    payload = (key, count)

                # 今天的格子：绿色外描边、加粗
                if d == today:
                    outline = Theme.SUCCESS
                    width = 2
                else:
                    outline = Theme.BORDER
                    width = 1

                cell_id = self.canvas.create_rectangle(
                    x0, y0, x0 + self.CELL, y0 + self.CELL,
                    fill=fill, outline=outline, width=width,
                )
                self._cell_lookup[cell_id] = payload

        # 渲染下方月度折线图
        self._render_line_chart(year, agg)

    def _render_line_chart(self, year: int, agg: dict):
        """折线图：本年 12 个月，每月一个点。x 轴 = 月份，y 轴 = 月度字数。"""
        c = self.line_canvas
        c.delete("all")
        self._line_lookup.clear()

        # 拿 12 个月数据（缺失的算 0）
        monthly = agg.get("monthly", {})
        months = []
        for m in range(1, 13):
            key = f"{year}-{m:02d}"
            months.append((key, m, int(monthly.get(key, 0))))

        # 取画布的实际像素尺寸（拉伸时也能用）
        c.update_idletasks()
        W = c.winfo_width() or int(c["width"])
        H = c.winfo_height() or int(c["height"])
        # 容器太小（窗口未完成布局）时跳过，等 <Configure> 重画
        if W < 200 or H < 80:
            return
        # 内边距
        PAD_L, PAD_R, PAD_T, PAD_B = 44, 16, 14, 28

        max_v = max((v for _, _, v in months), default=0)
        # 给上方留 10% 空间
        max_y = max(1, int(max_v * 1.1)) if max_v > 0 else 1

        # x 坐标：12 个月平均分布
        plot_w = W - PAD_L - PAD_R
        plot_h = H - PAD_T - PAD_B

        def x_at(m_idx: int) -> float:
            """m_idx: 0..11 → x 坐标"""
            if 12 == 1:
                return PAD_L + plot_w / 2
            return PAD_L + plot_w * m_idx / 11

        def y_at(v: int) -> float:
            return PAD_T + plot_h - (v / max_y) * plot_h

        # 画 y 轴参考线（4 条横线）
        for i in range(5):
            y = PAD_T + plot_h * i / 4
            c.create_line(PAD_L, y, W - PAD_R, y,
                           fill=Theme.BORDER, width=1, dash=(2, 4))
            v = int(max_y * (4 - i) / 4)
            c.create_text(PAD_L - 6, y, text=f"{v:,}", anchor="e",
                           fill=Theme.TEXT_DIM, font=Theme.FONT_SMALL)

        # 画 x 轴月份标签
        for idx, (_key, m, _v) in enumerate(months):
            x = x_at(idx)
            c.create_text(x, H - PAD_B + 12, text=f"{m}月",
                           fill=Theme.TEXT_DIM, font=Theme.FONT_SMALL)

        # 当前月份索引（非当前年则全部显示）
        today = date.today()
        current_month_idx = today.month - 1 if year == today.year else None
        # 折线/节点最多画到这个月（含），之后不画
        last_idx = current_month_idx if current_month_idx is not None else 11

        # 画连线：只连到当前月，未来月份不画
        if max_v > 0:
            pts = []
            for idx, (_key, _m, v) in enumerate(months):
                if idx > last_idx:
                    break
                pts.extend([x_at(idx), y_at(v)])
            if len(pts) >= 4:   # 至少两个点才连线
                c.create_line(*pts, fill=Theme.ACCENT, width=2)

        # 画节点 + 字数标签
        for idx, (key, _m, v) in enumerate(months):
            if idx > last_idx:
                continue
            x = x_at(idx)
            y = y_at(v)

            # 节点：当前月份用绿色描边
            r = 4
            outline = Theme.SUCCESS if idx == current_month_idx else Theme.ACCENT
            node_id = c.create_oval(
                x - r, y - r, x + r, y + r,
                fill=Theme.BG, outline=outline, width=2,
            )
            self._line_lookup[node_id] = (key, v)

            # 每个有数据的月份都标注字数
            if v > 0:
                c.create_text(x, y - 12, text=f"{v:,}",
                               fill=Theme.TEXT, font=Theme.FONT_SMALL)

    def _on_line_configure(self, _event):
        """画布尺寸变化时重绘折线图。"""
        # 只在 year tab 可见时重绘，避免不必要的 aggregate 调用
        if getattr(self, "_current_tab", None) == "year":
            try:
                agg = processor.aggregate()
                self._render_line_chart(self._year, agg)
            except Exception as e:
                print(f"[line] 重绘失败: {e}")

    def _on_line_motion(self, event):
        item = self.line_canvas.find_closest(event.x, event.y)
        if not item:
            self._hide_tooltip()
            return
        info = self._line_lookup.get(item[0])
        if not info:
            self._hide_tooltip()
            return
        month_key, count = info
        text = f"{month_key}\n{count:,} 字" if count else f"{month_key}\n无记录"
        self._show_tooltip(event.x_root + 14, event.y_root + 14, text)

    def _on_line_click(self, event):
        """点击折线图节点 → 切到月度统计 tab 并定位到那个月。"""
        item = self.line_canvas.find_closest(event.x, event.y)
        if not item:
            return
        info = self._line_lookup.get(item[0])
        if not info:
            return
        month_key, _count = info
        self._month_key = month_key
        self._render_month(month_key)
        self._switch_tab("month")

    # ============ 月度 tab ============
    def _build_month_tab(self, parent):
        bar = tk.Frame(parent, bg=Theme.BG)
        bar.pack(fill="x", padx=24, pady=(16, 12))
        self.lbl_month_title = tk.Label(
            bar, text="", bg=Theme.BG, fg=Theme.TEXT, font=Theme.FONT_TITLE
        )
        self.lbl_month_title.pack(side="left")
        nav = tk.Frame(bar, bg=Theme.BG)
        nav.pack(side="right")
        self.btn_prev_month = self._make_nav_button(nav, "‹", lambda: self._step_month(-1))
        self.btn_prev_month.pack(side="left", padx=(0, 6))
        self.btn_next_month = self._make_nav_button(nav, "›", lambda: self._step_month(1))
        self.btn_next_month.pack(side="left")

        # 日历容器
        self._month_grid = tk.Frame(parent, bg=Theme.BG)
        self._month_grid.pack(fill="both", expand=True, padx=24, pady=(0, 12))

        # 摘要
        self.lbl_month_summary = tk.Label(
            parent, text="", bg=Theme.BG, fg=Theme.TEXT_DIM, font=Theme.FONT_LABEL,
        )
        self.lbl_month_summary.pack(padx=24, pady=(0, 18), anchor="w")

    def _step_month(self, delta: int):
        year, month = map(int, self._month_key.split("-"))
        month += delta
        while month < 1:
            month += 12
            year -= 1
        while month > 12:
            month -= 12
            year += 1
        # 不让用户翻到未来
        today = date.today()
        if (year, month) > (today.year, today.month):
            return
        self._month_key = f"{year:04d}-{month:02d}"
        self._render_month(self._month_key)

    def _render_month(self, month_key: str):
        try:
            agg = processor.aggregate()
        except Exception as e:
            messagebox.showerror("错误", f"加载数据失败: {e}")
            return
        daily = agg["daily"]
        year, month = map(int, month_key.split("-"))
        month_total = agg["monthly"].get(month_key, 0)
        active = sum(1 for d, c in daily.items()
                     if d.startswith(month_key) and c > 0)

        self.lbl_month_title.config(text=f"{year} 年 {month} 月  ·  {month_total:,} 字")
        self.lbl_month_summary.config(
            text=f"{active} 天有记录   ·   平均 {month_total // max(1, active):,} 字/活跃日"
        )

        # 用本年的数据来算热力图阈值，让月度色阶和年度统一
        year_counts = [c for d, c in daily.items()
                       if d.startswith(f"{year}-") and c > 0]
        t1, t2, t3 = self._thresholds(year_counts)

        # 清空旧网格
        for w in self._month_grid.winfo_children():
            w.destroy()

        # 表头：周一至周日
        weekday_labels = ["一", "二", "三", "四", "五", "六", "日"]
        for col, lbl in enumerate(weekday_labels):
            tk.Label(self._month_grid, text=lbl, bg=Theme.BG, fg=Theme.TEXT_DIM,
                     font=Theme.FONT_SMALL).grid(row=0, column=col, sticky="nsew",
                                                  padx=2, pady=(0, 6))

        # 日历方格
        first = date(year, month, 1)
        if month == 12:
            next_first = date(year + 1, 1, 1)
        else:
            next_first = date(year, month + 1, 1)
        last = next_first - timedelta(days=1)
        today = date.today()

        # 第一行起始空格
        start_col = first.weekday()
        row = 1
        col = 0

        # 列权重均分
        for c in range(7):
            self._month_grid.columnconfigure(c, weight=1, uniform="day")

        # 先填前面的空白格
        for _ in range(start_col):
            tk.Frame(self._month_grid, bg=Theme.BG).grid(
                row=row, column=col, sticky="nsew", padx=2, pady=2)
            col += 1

        d = first
        while d <= last:
            key = d.strftime("%Y-%m-%d")
            count = daily.get(key, 0) if d <= today else None
            self._make_day_cell(self._month_grid, d, count, t1, t2, t3).grid(
                row=row, column=col, sticky="nsew", padx=2, pady=2)
            col += 1
            if col >= 7:
                col = 0
                row += 1
            d += timedelta(days=1)

        # 行权重均分
        total_rows = row + (1 if col > 0 else 0)
        for r in range(1, total_rows):
            self._month_grid.rowconfigure(r, weight=1, uniform="week")

    def _make_day_cell(self, parent, day: date, count: int | None,
                        t1: int, t2: int, t3: int) -> tk.Widget:
        """日历方格：日期居中大字粗体；字数小字在下方；背景=热力色；今天加边框。"""
        if count is None:
            bg = "#1A1C22"   # 未来日期：低亮度灰背景
            count_text = "—"
            count_fg = Theme.TEXT_DIM
            date_fg = Theme.TEXT_DIM
        else:
            bg = self._color_for(count)
            count_text = f"{count:,}" if count > 0 else "0"
            # 字数颜色：>0 用接近白，=0 用更暗
            count_fg = "#FFFFFF" if count > 0 else Theme.TEXT_DIM
            date_fg = "#FFFFFF" if count > 0 else Theme.TEXT

        is_today = (day == date.today())
        border = Theme.ACCENT if is_today else Theme.BORDER

        frame = tk.Frame(parent, bg=bg, highlightthickness=1,
                          highlightbackground=border)
        # 用一个内部容器把内容垂直居中
        inner = tk.Frame(frame, bg=bg)
        inner.pack(expand=True)
        # 日期：居中、大字、粗体
        tk.Label(inner, text=str(day.day), bg=bg, fg=date_fg,
                 font=("PingFang SC", 20, "bold")).pack(pady=(0, 2))
        # 字数：居中、小字
        tk.Label(inner, text=count_text, bg=bg, fg=count_fg,
                 font=("PingFang SC", 11)).pack()
        return frame

    # ============ Tooltip（年度 tab 用）============
    def _on_canvas_motion(self, event):
        item = self.canvas.find_closest(event.x, event.y)
        if not item:
            self._hide_tooltip()
            return
        info = self._cell_lookup.get(item[0])
        if not info:
            self._hide_tooltip()
            return
        date_str, count = info
        if count is None:
            text = f"{date_str}\n（未到）"
        elif count > 0:
            text = f"{date_str}\n{count:,} 字"
        else:
            text = f"{date_str}\n无记录"
        self._show_tooltip(event.x_root + 14, event.y_root + 14, text)

    def _show_tooltip(self, x: int, y: int, text: str):
        if self._tooltip is None:
            self._tooltip = tk.Toplevel(self)
            self._tooltip.overrideredirect(True)
            self._tooltip.attributes("-topmost", True)
            self._tooltip.configure(bg=Theme.SURFACE_HI)
            self._tooltip_label = tk.Label(
                self._tooltip, bg=Theme.SURFACE_HI, fg=Theme.TEXT,
                font=Theme.FONT_SMALL, padx=8, pady=4, justify="left",
            )
            self._tooltip_label.pack()
        if self._tooltip_label is not None:
            self._tooltip_label.config(text=text)
        self._tooltip.geometry(f"+{x}+{y}")
        self._tooltip.deiconify()

    def _hide_tooltip(self):
        if self._tooltip is not None:
            self._tooltip.withdraw()


# ========== 设置窗口 ==========
class SettingsWindow(tk.Toplevel):
    """配置编辑器：浏览路径 / 修改设备名 / 设置自动清空时间。

    standalone=True 表示独立子进程模式（parent 是隐藏的临时 root），
    此时不做 transient（transient 到隐藏窗口会导致自己也不显示）。
    """

    def __init__(self, parent, standalone: bool = False):
        super().__init__(parent)
        self.title("设置")
        self.configure(bg=Theme.BG)
        self.geometry("560x600")
        self.resizable(False, False)
        if not standalone:
            self.transient(parent)
            self.grab_set()

        # 当前配置（用于回填）
        from wc_core import config_path as _cfg_path
        from wc_core import load_config as _load_cfg
        from wc_core import save_config as _save_cfg
        from wc_core import default_csv_path, default_json_path
        from wc_core import sync_lua_plaintext as _sync_lua
        self._save_config = _save_cfg
        self._sync_lua = _sync_lua
        self._cfg = _load_cfg()
        self._default_csv = str(default_csv_path())
        self._default_json = str(default_json_path())

        # 用 StringVar 存编辑中的值（空字符串 = 用默认）
        self.var_json = tk.StringVar(value=self._cfg._raw.get("json_path", ""))
        self.var_device = tk.StringVar(value=self._cfg._raw.get("device_id", ""))
        self.var_auto_clear = tk.BooleanVar(value=bool(self._cfg.auto_clear_csv))
        self.var_hour = tk.StringVar(value=str(self._cfg.clear_hour))
        self.var_minute = tk.StringVar(value=str(self._cfg.clear_minute))
        self.var_interval_days = tk.StringVar(value=str(self._cfg.clear_interval_days))
        self.var_enable_log = tk.BooleanVar(value=bool(self._cfg.enable_log))
        self.var_plaintext = tk.BooleanVar(value=bool(self._cfg.enable_plaintext))

        self._build_ui()

    # -------- UI 构造 --------
    def _build_ui(self):
        # 标题
        tk.Label(self, text="⚙ 设置", bg=Theme.BG, fg=Theme.TEXT,
                 font=Theme.FONT_TITLE).pack(anchor="w", padx=24, pady=(20, 14))

        # 底部按钮栏：先 pack 到底部，保证永远可见、不被内容挤出
        bar = tk.Frame(self, bg=Theme.BG)
        bar.pack(side="bottom", fill="x", padx=24, pady=18)
        cancel = self._action_button(bar, "取消", self.destroy, primary=False)
        cancel.pack(side="right")
        save = self._action_button(bar, "保存", self._on_save, primary=True)
        save.pack(side="right", padx=(0, 8))
        # 分隔线
        tk.Frame(self, bg=Theme.BORDER, height=1).pack(
            side="bottom", fill="x", padx=24, pady=(0, 0))

        # 选项卡容器
        self._current_tab = 0
        tab_container = tk.Frame(self, bg=Theme.BG)
        tab_container.pack(fill="both", expand=True, padx=24, pady=(0, 12))

        # 选项卡按钮
        tab_bar = tk.Frame(tab_container, bg=Theme.BG)
        tab_bar.pack(fill="x", pady=(0, 12))

        self._tab_buttons = []
        for idx, name in enumerate(["基础设置", "其他"]):
            btn = tk.Label(
                tab_bar, text=name, bg=Theme.SURFACE, fg=Theme.TEXT,
                font=("PingFang SC", 12, "bold"), padx=20, pady=8,
                cursor="hand2"
            )
            btn.pack(side="left", padx=(0, 8))
            btn.bind("<Button-1>", lambda e, i=idx: self._switch_tab(i))
            self._tab_buttons.append(btn)

        # 选项卡内容区：用一个 holder 承载，两个 frame 用 grid 叠放在同一格，靠 tkraise 切换
        tab_holder = tk.Frame(tab_container, bg=Theme.BG)
        tab_holder.pack(fill="both", expand=True)
        tab_holder.grid_rowconfigure(0, weight=1)
        tab_holder.grid_columnconfigure(0, weight=1)

        self._tab_frames = []
        for i in range(2):
            frame = tk.Frame(tab_holder, bg=Theme.BG)
            frame.grid(row=0, column=0, sticky="nsew")
            self._tab_frames.append(frame)

        # 基础设置 tab
        self._field_count = 0
        self._scrollable = self._tab_frames[0]
        self._build_csv_row()
        self._build_json_row()
        self._build_device_row()

        # 其他 tab
        self._field_count = 0  # 重置计数器
        self._scrollable = self._tab_frames[1]
        self._build_clear_row()
        self._build_log_row()
        self._build_plaintext_row()

        # 默认显示第一个 tab
        self._switch_tab(0)

    def _switch_tab(self, idx: int):
        """切换选项卡"""
        self._current_tab = idx
        # 更新按钮样式
        for i, btn in enumerate(self._tab_buttons):
            if i == idx:
                btn.config(bg=Theme.SURFACE_HI, fg=Theme.TEXT)
            else:
                btn.config(bg=Theme.SURFACE, fg=Theme.TEXT_DIM)
        # tkraise 把目标 frame 提到最前（grid 叠放，不会有刷新问题）
        self._tab_frames[idx].tkraise()

    def _build_csv_row(self):
        wrap = self._field_wrap(
            "CSV 文件路径",
            "RIME 上屏数据缓冲文件，必须和 words_counter.lua 里 CUSTOM_CSV_PATH 一致。"
            "为防止误改，此路径锁定不可编辑。",
        )
        path_frame = tk.Frame(wrap, bg=Theme.SURFACE,
                               highlightthickness=1, highlightbackground=Theme.BORDER)
        path_frame.pack(fill="x", pady=(6, 0))
        tk.Label(path_frame, text=self._default_csv, bg=Theme.SURFACE, fg=Theme.TEXT_DIM,
                 font=("Menlo", 11), anchor="w", padx=10, pady=8).pack(fill="x")

    def _build_json_row(self):
        wrap = self._field_wrap(
            "历史 JSON 文件路径",
            "字数累计数据存放位置。多设备同步建议放云盘。"
            "留空 = 默认放应用配置目录，可通过【状态栏-配置文件夹与日志】打开目录。",
        )
        row = tk.Frame(wrap, bg=Theme.BG)
        row.pack(fill="x", pady=(6, 0))

        entry_frame = tk.Frame(row, bg=Theme.SURFACE,
                                highlightthickness=1, highlightbackground=Theme.BORDER)
        entry_frame.pack(side="left", fill="x", expand=True)
        self.entry_json = tk.Entry(
            entry_frame, textvariable=self.var_json,
            bg=Theme.SURFACE, fg=Theme.TEXT, insertbackground=Theme.TEXT,
            relief="flat", bd=0, font=("Menlo", 11), highlightthickness=0,
        )
        self.entry_json.pack(fill="x", padx=10, pady=8)

        # 占位提示
        self._update_json_placeholder()
        self.var_json.trace_add("write", lambda *_: self._update_json_placeholder())

        browse_btn = self._small_button(row, "浏览…", self._pick_json_path)
        browse_btn.pack(side="left", padx=(8, 0))

    def _update_json_placeholder(self):
        """JSON 输入框为空时显示默认路径作为提示色。"""
        if not self.var_json.get():
            # 暂时切色到 TEXT_DIM 显示默认路径
            self.entry_json.config(fg=Theme.TEXT_DIM)
        else:
            self.entry_json.config(fg=Theme.TEXT)

    def _build_device_row(self):
        wrap = self._field_wrap(
            "本机设备名",
            "多设备同步时用来区分。留空 = 用机器名（hostname）。",
        )
        entry_frame = tk.Frame(wrap, bg=Theme.SURFACE,
                                highlightthickness=1, highlightbackground=Theme.BORDER)
        entry_frame.pack(fill="x", pady=(6, 0))
        tk.Entry(
            entry_frame, textvariable=self.var_device,
            bg=Theme.SURFACE, fg=Theme.TEXT, insertbackground=Theme.TEXT,
            relief="flat", bd=0, font=("PingFang SC", 12), highlightthickness=0,
        ).pack(fill="x", padx=10, pady=8)
        # 提示当前生效设备名
        tk.Label(wrap, text=f"当前生效: {self._cfg.device_id}",
                 bg=Theme.BG, fg=Theme.TEXT_DIM,
                 font=Theme.FONT_SMALL).pack(anchor="w", pady=(4, 0))

    def _build_clear_row(self):
        def _make_clear_switch(parent):
            self._clear_switch = self._make_switch(
                parent, self.var_auto_clear, on_toggle=self._toggle_clear_visibility)
            return self._clear_switch

        wrap = self._field_wrap(
            "自动清空 CSV",
            "每隔 N 天的 HH:MM 自动清空 CSV 缓冲（统计数据不丢，已存进 JSON）。",
            right_widget_factory=_make_clear_switch,
        )

        # 间隔天数 + 时间（用一个容器包起来，方便整体显隐）
        self._clear_detail = tk.Frame(wrap, bg=Theme.BG)
        # 注意：这里先不 pack，由 _toggle_clear_visibility 控制

        time_row = tk.Frame(self._clear_detail, bg=Theme.BG)
        time_row.pack(fill="x", pady=(10, 0))
        tk.Label(time_row, text="每隔", bg=Theme.BG, fg=Theme.TEXT_DIM,
                 font=Theme.FONT_LABEL).pack(side="left")

        # 间隔天数 spinbox
        days_frame = tk.Frame(time_row, bg=Theme.SURFACE,
                               highlightthickness=1, highlightbackground=Theme.BORDER)
        days_frame.pack(side="left", padx=(6, 0))
        tk.Spinbox(
            days_frame, from_=1, to=365, textvariable=self.var_interval_days, width=4,
            bg=Theme.SURFACE, fg=Theme.TEXT, insertbackground=Theme.TEXT,
            buttonbackground=Theme.SURFACE_HI,
            relief="flat", bd=0, font=("PingFang SC", 12),
            highlightthickness=0, justify="center",
        ).pack(padx=4, pady=4)
        tk.Label(time_row, text="天的", bg=Theme.BG, fg=Theme.TEXT_DIM,
                 font=Theme.FONT_LABEL).pack(side="left", padx=(4, 0))

        # 时分 spinbox
        for var, suffix, max_v in ((self.var_hour, "时", 23), (self.var_minute, "分", 59)):
            spin_frame = tk.Frame(time_row, bg=Theme.SURFACE,
                                   highlightthickness=1, highlightbackground=Theme.BORDER)
            spin_frame.pack(side="left", padx=(6, 0))
            tk.Spinbox(
                spin_frame, from_=0, to=max_v, textvariable=var, width=4,
                bg=Theme.SURFACE, fg=Theme.TEXT, insertbackground=Theme.TEXT,
                buttonbackground=Theme.SURFACE_HI,
                relief="flat", bd=0, font=("PingFang SC", 12),
                highlightthickness=0,
                justify="center",
            ).pack(padx=4, pady=4)
            tk.Label(time_row, text=suffix, bg=Theme.BG, fg=Theme.TEXT_DIM,
                     font=Theme.FONT_LABEL).pack(side="left", padx=(4, 0))

        # 提示：1 = 每天
        tk.Label(self._clear_detail, text="例如：1=每天清空，可随意自定义间隔天数。",
                 bg=Theme.BG, fg=Theme.TEXT_DIM,
                 font=Theme.FONT_SMALL).pack(anchor="w", pady=(6, 0))

        # 根据当前开关状态决定是否显示时间区
        self._toggle_clear_visibility()

    def _toggle_clear_visibility(self):
        """根据"自动清空"开关，显隐时间间隔设置区。"""
        if self.var_auto_clear.get():
            self._clear_detail.pack(fill="x")
        else:
            self._clear_detail.pack_forget()

    def _build_log_row(self):
        wrap = self._field_wrap(
            "启用日志",
            "记录运行日志到 app.log（位于配置文件夹）。出问题时可以提供给开发者排查。",
            right_widget_factory=lambda p: self._make_switch(p, self.var_enable_log),
        )

    def _build_plaintext_row(self):
        wrap = self._field_wrap(
            "明文版",
            "⭐开启后 CSV 第三列记录上屏原文。每次切换后，必须执行“重新部署”rime输入法才能生效。",
            right_widget_factory=lambda p: self._make_switch(p, self.var_plaintext),
        )

    def _make_switch(self, parent, var: tk.BooleanVar, on_toggle=None) -> tk.Frame:
        """用 Canvas 画一个 iOS 风格的开关。点击切换 var，并回调 on_toggle。"""
        W, H = 46, 26
        R = H // 2
        canvas = tk.Canvas(parent, width=W, height=H, bg=Theme.BG,
                           highlightthickness=0, bd=0, cursor="hand2")

        def redraw():
            canvas.delete("all")
            on = bool(var.get())
            track = Theme.ACCENT if on else Theme.BORDER
            # 圆角轨道（两个圆 + 中间矩形）
            canvas.create_oval(1, 1, H - 1, H - 1, fill=track, outline=track)
            canvas.create_oval(W - H + 1, 1, W - 1, H - 1, fill=track, outline=track)
            canvas.create_rectangle(R, 1, W - R, H - 1, fill=track, outline=track)
            # 滑块
            knob_x = (W - R - 1) if on else (R + 1)
            canvas.create_oval(knob_x - (R - 3), R - (R - 3),
                               knob_x + (R - 3), R + (R - 3),
                               fill="#FFFFFF", outline="#FFFFFF")

        def toggle(_e=None):
            var.set(not var.get())
            redraw()
            if on_toggle:
                on_toggle()

        canvas.bind("<Button-1>", toggle)
        redraw()
        canvas._redraw = redraw  # 暴露给外部，必要时手动刷新
        return canvas

    def _field_wrap(self, title: str, hint: str, right_widget_factory=None) -> tk.Frame:
        """right_widget_factory: callable(parent) -> widget，放在标题行右侧。"""
        parent = getattr(self, '_scrollable', self)  # 有滚动区就用滚动区，没有就用 self

        # 字段之间加浅色分隔线（第一个字段前不加）
        # padx=0：外层 tab_container 已有 24 边距，这里不再叠加
        if getattr(self, '_field_count', 0) > 0:
            tk.Frame(parent, bg=Theme.BORDER, height=1).pack(
                fill="x", padx=0, pady=(0, 14))
        self._field_count = getattr(self, '_field_count', 0) + 1

        wrap = tk.Frame(parent, bg=Theme.BG)
        wrap.pack(fill="x", padx=0, pady=(0, 14))

        # 标题行：title 靠左，可选 right_widget 靠右
        title_row = tk.Frame(wrap, bg=Theme.BG)
        title_row.pack(fill="x")
        tk.Label(title_row, text=title, bg=Theme.BG, fg=Theme.TEXT,
                 font=("PingFang SC", 12, "bold")).pack(side="left")
        if right_widget_factory is not None:
            right_widget_factory(title_row).pack(side="right")

        tk.Label(wrap, text=hint, bg=Theme.BG, fg=Theme.TEXT_DIM,
                 font=Theme.FONT_SMALL, wraplength=520, justify="left").pack(
            anchor="w", pady=(2, 0))
        return wrap

    def _action_button(self, parent, text: str, cmd, primary: bool) -> tk.Frame:
        bg = Theme.ACCENT if primary else Theme.SURFACE
        fg = "#0F1218" if primary else Theme.TEXT
        active_bg = Theme.ACCENT_HI if primary else Theme.SURFACE_HI
        frame = tk.Frame(parent, bg=bg)
        label = tk.Label(frame, text=text, bg=bg, fg=fg,
                          font=("PingFang SC", 12, "bold"),
                          cursor="hand2", padx=22, pady=10)
        label.pack()
        for w in (frame, label):
            w.bind("<Enter>", lambda _e: (frame.config(bg=active_bg),
                                            label.config(bg=active_bg)))
            w.bind("<Leave>", lambda _e: (frame.config(bg=bg),
                                            label.config(bg=bg)))
            w.bind("<Button-1>", lambda _e: self._safe_call(cmd))
        return frame

    def _safe_call(self, cmd):
        """执行回调，捕获异常并弹错误框（避免异常被 Tk 事件循环静默吞掉）。"""
        try:
            cmd()
        except Exception as e:
            import traceback
            traceback.print_exc()
            messagebox.showerror("出错了", f"{e}", parent=self)

    def _small_button(self, parent, text: str, cmd) -> tk.Frame:
        frame = tk.Frame(parent, bg=Theme.SURFACE)
        label = tk.Label(frame, text=text, bg=Theme.SURFACE, fg=Theme.TEXT,
                          font=("PingFang SC", 11),
                          cursor="hand2", padx=12, pady=8)
        label.pack()
        for w in (frame, label):
            w.bind("<Enter>", lambda _e: (frame.config(bg=Theme.SURFACE_HI),
                                            label.config(bg=Theme.SURFACE_HI)))
            w.bind("<Leave>", lambda _e: (frame.config(bg=Theme.SURFACE),
                                            label.config(bg=Theme.SURFACE)))
            w.bind("<Button-1>", lambda _e: cmd())
        return frame

    # -------- 行为 --------
    def _pick_json_path(self):
        from tkinter import filedialog
        # 起点用当前生效路径所在目录
        current = self.var_json.get() or self._default_json
        initial_dir = os.path.dirname(current) or os.path.expanduser("~")
        # 用户选 JSON 文件（保存对话框，可新建可选已有）
        path = filedialog.asksaveasfilename(
            parent=self,
            title="选择历史 JSON 文件存放位置",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            initialdir=initial_dir,
            initialfile=os.path.basename(current) or "words_count_history.json",
            confirmoverwrite=False,   # 选已有文件不弹覆盖警告
        )
        if path:
            self.var_json.set(path)

    def _on_save(self):
        # 校验时间
        try:
            hour = int(self.var_hour.get())
            minute = int(self.var_minute.get())
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "清空时间必须是有效的小时（0-23）和分钟（0-59）",
                                  parent=self)
            return

        # 校验间隔天数
        try:
            interval_days = int(self.var_interval_days.get())
            if interval_days < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "间隔天数必须是大于等于 1 的整数",
                                  parent=self)
            return

        new_json = self.var_json.get().strip()
        old_json_path = self._cfg.json_path

        # 如果修改了 JSON 路径，把现有数据移动到新位置（移动 = 旧路径不再保留）
        if new_json:
            new_json_path = Path(new_json).expanduser()
            if str(new_json_path) != str(old_json_path):
                try:
                    new_json_path.parent.mkdir(parents=True, exist_ok=True)
                    if old_json_path.exists():
                        import shutil
                        if new_json_path.exists():
                            # 新位置已有文件：不覆盖，仅删除旧文件（用新位置的数据）
                            old_json_path.unlink()
                        else:
                            # 移动旧文件到新位置（旧路径清空）
                            shutil.move(str(old_json_path), str(new_json_path))
                except Exception as e:
                    if not messagebox.askyesno(
                        "迁移失败",
                        f"无法把现有数据移动到新位置：\n{e}\n\n继续保存配置吗？",
                        parent=self,
                    ):
                        return

        # 写配置（记录旧 device_id，用于保存后重命名 JSON 节点）
        old_device_id = self._cfg.device_id
        try:
            self._save_config({
                "json_path": new_json,
                "device_id": self.var_device.get().strip(),
                "auto_clear_csv": bool(self.var_auto_clear.get()),
                "clear_hour": hour,
                "clear_minute": minute,
                "clear_interval_days": interval_days,
                "enable_log": bool(self.var_enable_log.get()),
                "enable_plaintext": bool(self.var_plaintext.get()),
            })
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}", parent=self)
            return

        # 设备名变更：把 JSON 里的旧节点重命名，并热更新 processor
        new_device_id = load_config().device_id
        if old_device_id != new_device_id:
            rename_device(str(processor.json_path), old_device_id, new_device_id)
            processor.device_id = new_device_id

        # 同步明文开关到 Lua 文件
        ok, msg = self._sync_lua(bool(self.var_plaintext.get()))
        if not ok:
            messagebox.showwarning(
                "Lua 同步提示",
                f"设置已保存，但自动同步 Lua 文件失败：\n{msg}\n\n"
                "请手动修改 words_counter.lua 中的 ENABLE_PLAINTEXT 值。",
                parent=self,
            )

        messagebox.showinfo(
            "已保存",
            "设置已保存并即时生效。\n（若有打开的“详细数据”窗口，关闭后重新打开即可看到新路径数据。）",
            parent=self,
        )
        self.destroy()


# ========== 主界面 ==========
class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("字数统计")
        self.configure(bg=Theme.BG)
        self.geometry("460x500")
        self.minsize(420, 480)
        self.signal_file = SIGNAL_FILE
        self.withdraw()
        self.protocol("WM_DELETE_WINDOW", self.hide_window)

        self.speed_tester = SpeedTester()
        self._observers: list[Observer] = []
        self._speed_after_id = None
        self._stat_values: dict[str, tk.Label] = {}

        self._build_ui()

        # 启动前先把积压的 CSV 处理一次
        processor.process_data()
        self._refresh_display()

        # 订阅数据更新（来自 wc_core 的 bus）
        bus.subscribe(self._on_data_update)

        self._start_signal_checker()
        self._start_file_monitor()
        self._start_scheduler()
        self._bind_exit()

    # -------- UI --------
    def _build_ui(self):
        # 顶部标题
        header = tk.Frame(self, bg=Theme.BG)
        header.pack(fill="x", padx=24, pady=(22, 18))
        tk.Label(header, text="📝", bg=Theme.BG, fg=Theme.ACCENT,
                 font=("PingFang SC", 22)).pack(side="left", padx=(0, 8))
        tk.Label(header, text="字数统计", bg=Theme.BG, fg=Theme.TEXT,
                 font=Theme.FONT_TITLE).pack(side="left")

        # 2×2 卡片网格
        cards = tk.Frame(self, bg=Theme.BG)
        cards.pack(fill="x", padx=20)
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)

        self._make_stat_card(cards, "今日", "daily", row=0, col=0)
        self._make_stat_card(cards, "本月", "monthly", row=0, col=1)
        self._make_stat_card(cards, "本年", "yearly", row=1, col=0)
        self._make_stat_card(cards, "累计", "total", row=1, col=1)

        # 测速面板
        speed_card = tk.Frame(self, bg=Theme.SURFACE, bd=0,
                              highlightthickness=1, highlightbackground=Theme.BORDER)
        speed_card.pack(fill="x", padx=20, pady=(14, 0))
        inner = tk.Frame(speed_card, bg=Theme.SURFACE)
        inner.pack(fill="x", padx=16, pady=14)

        head = tk.Frame(inner, bg=Theme.SURFACE)
        head.pack(fill="x")
        self._speed_dot = tk.Label(head, text="●", bg=Theme.SURFACE,
                                   fg=Theme.TEXT_DIM, font=("PingFang SC", 12))
        self._speed_dot.pack(side="left")
        tk.Label(head, text="  输入速度", bg=Theme.SURFACE, fg=Theme.TEXT_DIM,
                 font=Theme.FONT_LABEL).pack(side="left")
        self._speed_value = tk.Label(
            head, text="未测速", bg=Theme.SURFACE, fg=Theme.TEXT,
            font=("PingFang SC", 14, "bold"),
        )
        self._speed_value.pack(side="right")

        # 按钮行
        actions = tk.Frame(self, bg=Theme.BG)
        actions.pack(fill="x", padx=20, pady=(18, 12))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)

        self.btn_speed = self._make_button(
            actions, "开始测速", self._toggle_speed, primary=True,
        )
        self.btn_speed.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.btn_history = self._make_button(
            actions, "历史记录", lambda: HistoryWindow(self), primary=False,
        )
        self.btn_history.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        # 底部署名
        footer = tk.Label(self, text="by hyuan", bg=Theme.BG, fg=Theme.TEXT_DIM,
                          font=Theme.FONT_SMALL)
        footer.pack(pady=(4, 14))

    def _make_stat_card(self, parent, title: str, key: str, row: int, col: int):
        card = tk.Frame(parent, bg=Theme.SURFACE,
                        highlightthickness=1, highlightbackground=Theme.BORDER)
        card.grid(row=row, column=col, sticky="nsew", padx=4, pady=4, ipadx=4, ipady=4)
        wrap = tk.Frame(card, bg=Theme.SURFACE)
        wrap.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(wrap, text=title, bg=Theme.SURFACE, fg=Theme.TEXT_DIM,
                 font=Theme.FONT_LABEL).pack(anchor="w")
        value = tk.Label(wrap, text="—", bg=Theme.SURFACE, fg=Theme.TEXT,
                         font=Theme.FONT_NUMBER, anchor="w")
        value.pack(anchor="w", pady=(4, 0))
        self._stat_values[key] = value

    def _make_button(self, parent, text: str, cmd, primary: bool):
        """用 Frame + Label 模拟按钮——macOS Tk 的 tk.Button 不支持自定义颜色。"""
        bg = Theme.ACCENT if primary else Theme.SURFACE
        fg = "#0F1218" if primary else Theme.TEXT
        active_bg = Theme.ACCENT_HI if primary else Theme.SURFACE_HI

        frame = tk.Frame(parent, bg=bg, bd=0, highlightthickness=0)
        label = tk.Label(
            frame, text=text, bg=bg, fg=fg,
            font=("PingFang SC", 12, "bold"),
            cursor="hand2", padx=12, pady=10,
        )
        label.pack(fill="both", expand=True)

        def set_bg(c):
            frame.config(bg=c)
            label.config(bg=c)

        def on_enter(_e):
            set_bg(active_bg)

        def on_leave(_e):
            set_bg(bg)

        def on_click(_e):
            try:
                cmd()
            except Exception as e:
                print(f"[button] {e}")

        for w in (frame, label):
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
            w.bind("<Button-1>", on_click)

        # 暴露成 button-like API 给主程序的 _toggle_speed 用
        frame._label = label
        frame._set_bg = set_bg
        frame._set_text = lambda t: label.config(text=t)
        return frame

    def _refresh_display(self):
        try:
            agg = processor.aggregate()
        except Exception as e:
            print(f"[ui] 读取聚合数据失败: {e}")
            return
        now = datetime.now()
        self._stat_values["daily"].config(
            text=f"{agg['daily'].get(now.strftime('%Y-%m-%d'), 0):,}")
        self._stat_values["monthly"].config(
            text=f"{agg['monthly'].get(now.strftime('%Y-%m'), 0):,}")
        self._stat_values["yearly"].config(
            text=f"{agg['yearly'].get(now.strftime('%Y'), 0):,}")
        self._stat_values["total"].config(text=f"{agg['total']:,}")
        if not self.speed_tester.active:
            self._speed_value.config(
                text=self.speed_tester.last_speed_label, fg=Theme.TEXT,
            )
            self._speed_dot.config(fg=Theme.TEXT_DIM)

    def _on_data_update(self, _payload):
        # 来自后台线程，必须绕回主线程
        self.after(0, self._refresh_display)

    # -------- 测速 --------
    def _toggle_speed(self):
        if not self.speed_tester.active:
            self.speed_tester.start()
            self.btn_speed._set_text("结束测速")
            self.btn_speed._set_bg(Theme.DANGER)
            # 重新绑定 hover 颜色
            self._rebind_speed_hover(Theme.DANGER, "#FF8585")
            self._speed_dot.config(fg=Theme.ACCENT)
            self._speed_value.config(text="测速中…", fg=Theme.ACCENT)
            self._tick_speed()
        else:
            self.speed_tester.stop()
            self.btn_speed._set_text("开始测速")
            self.btn_speed._set_bg(Theme.ACCENT)
            self._rebind_speed_hover(Theme.ACCENT, Theme.ACCENT_HI)
            self._speed_dot.config(fg=Theme.TEXT_DIM)
            self._speed_value.config(
                text=self.speed_tester.last_speed_label, fg=Theme.TEXT,
            )
            if self._speed_after_id is not None:
                self.after_cancel(self._speed_after_id)
                self._speed_after_id = None

    def _rebind_speed_hover(self, normal_bg: str, hover_bg: str):
        """重新绑定 btn_speed 的 hover 颜色（测速开关切换时调）。"""
        btn = self.btn_speed
        for w in (btn, btn._label):
            w.unbind("<Enter>")
            w.unbind("<Leave>")
            w.bind("<Enter>", lambda _e, c=hover_bg: btn._set_bg(c))
            w.bind("<Leave>", lambda _e, c=normal_bg: btn._set_bg(c))

    def _tick_speed(self):
        if not self.speed_tester.active:
            return
        speed = self.speed_tester.current_speed()
        color = Theme.ACCENT
        if 800 <= speed <= 1500:
            color = Theme.SUCCESS
        elif speed > 1500:
            color = Theme.WARN
        self._speed_value.config(text=f"{speed:.0f} 字/小时", fg=color)
        self._speed_dot.config(fg=color)
        self._speed_after_id = self.after(2000, self._tick_speed)

    # -------- 信号文件检查 --------
    def _start_signal_checker(self):
        # 用 Tk 自身的 after 调度，省一个线程，也避免线程访问 Tk 的麻烦
        def check():
            try:
                if os.path.exists(self.signal_file):
                    self.deiconify()
                    self.lift()
                    self.attributes("-topmost", 1)
                    self.after(100, lambda: self.attributes("-topmost", 0))
                    try:
                        os.remove(self.signal_file)
                    except OSError:
                        pass
            finally:
                self.after(300, check)

        self.after(300, check)

    def hide_window(self):
        self.withdraw()
        if os.path.exists(self.signal_file):
            try:
                os.remove(self.signal_file)
            except OSError:
                pass

    # -------- 后台监控 --------
    def _start_file_monitor(self):
        observer = Observer()
        handler = CSVHandler(on_change=processor.process_data)
        observer.schedule(handler, path=os.path.dirname(CSV_FILE), recursive=False)
        observer.start()
        self._observers.append(observer)

        # 后台轮询兜底：watchdog 在某些路径上偶尔失败，每 1.5 秒兜底处理一次
        self._poller_running = True
        def poll_loop():
            while self._poller_running:
                try:
                    processor.process_data()
                except Exception as e:
                    print(f"[gui poller] {e}")
                time.sleep(1.5)
        threading.Thread(target=poll_loop, daemon=True, name="gui-csv-poller").start()

    def _start_scheduler(self):
        if not config.auto_clear_csv:
            return

        def clear_csv_daily():
            try:
                processor.process_data()
                processor.clear_csv()
                print(f"[{datetime.now()}] CSV 已清空")
            except Exception as e:
                print(f"[scheduler] 清空失败: {e}")

        schedule_daily(clear_csv_daily, hour=config.clear_hour, minute=config.clear_minute)

    # -------- 退出 --------
    def _bind_exit(self):
        self.bind_all("<Control-q>", lambda _e=None: self.full_exit())
        self.createcommand("exit", self.full_exit)

    def full_exit(self, *_args):
        print("[System] 正在执行安全退出流程...")
        try:
            self._poller_running = False
            bus.unsubscribe(self._on_data_update)
            for obs in self._observers:
                try:
                    obs.stop()
                    obs.join(timeout=1)
                except Exception as e:
                    print(f"[exit] 关闭 observer 失败: {e}")
            if sys.platform == "darwin" and not getattr(sys, "frozen", False):
                # 开发模式下用 pkill 兜底防止僵尸进程；frozen 应用由系统管理生命周期
                subprocess.run(
                    ["pkill", "-f", os.path.basename(__file__)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        finally:
            try:
                self.destroy()
            except Exception:
                pass
            sys.exit(0)

    # -------- 外部可调用 --------
    @staticmethod
    def clear_csv() -> bool:
        processor.process_data()
        return processor.clear_csv()


if __name__ == "__main__":
    if "--clear-csv" in sys.argv:
        ok = Application.clear_csv()
        sys.exit(0 if ok else 1)
    app = Application()
    app.mainloop()
