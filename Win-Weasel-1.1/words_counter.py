"""
版本: 字数统计工具-小狼毫-v1.1
作者: hyuan
Github仓库: https://github.com/hyuan42/Rime-words-counter
时间: 2026-06-26

依赖: pip install portalocker pystray pillow pywin32 watchdog
"""

from __future__ import annotations

import csv
import json
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import defaultdict  # kept for potential downstream use
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Optional

import portalocker
import pystray
import win32api
import win32con
import win32gui
from PIL import Image, ImageDraw, ImageFont
from watchdog.events import PatternMatchingEventHandler
from watchdog.observers import Observer


# ================== 路径与配置 ==================
APP_NAME = "RimeWordsCounter"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_support_dir() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME


def rime_user_dir() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "Rime"


def default_csv_path() -> Path:
    return rime_user_dir() / "py_wordscounter" / "words_input.csv"


def default_json_path() -> Path:
    return app_support_dir() / "words_count_history.json"


def config_path() -> Path:
    return app_support_dir() / "config.json"


_DEFAULT_CONFIG_TEMPLATE = """{
  "_comment": "字数统计工具配置文件。修改后重启程序生效。可以直接在【设置】窗口中修改对应的参数，或者在该文件中手动修改",
  "csv_path": "",
  "_csv_path_help": "RIME 上屏 CSV 路径。留空 = 自动用 RIME 用户目录下的 py_wordscounter/words_input.csv，要和 words_counter.lua 里的 CUSTOM_CSV_PATH 保持一致。",
  "json_path": "",
  "_json_path_help": "字数统计 JSON 历史文件。留空 = 放到本应用配置目录。多设备同步建议改到 OneDrive/坚果云/Dropbox 等云盘路径。",
  "device_id": "",
  "_device_id_help": "本设备唯一标识。留空 = 使用机器名（hostname）。多设备时务必各不相同。",
  "auto_clear_csv": true,
  "clear_hour": 0,
  "clear_minute": 0,
  "clear_interval_days": 1,
  "_clear_help": "auto_clear_csv=true 时，每隔 clear_interval_days 天的 clear_hour:clear_minute 自动清空 CSV。1=每天清空，7=每周清空。",
  "enable_log": true,
  "_enable_log_help": "是否启用日志功能（写入 app.log 文件，方便排查问题）。默认开启。",
  "enable_plaintext": false,
  "_enable_plaintext_help": "是否开启明文版采集（开启后 CSV 第三列记录上屏原文。每次切换后，必须执行“重新部署”rime输入法才能生效"
}
"""


class _Config:
    def __init__(self, raw: dict):
        self._raw = raw

    @property
    def csv_path(self) -> Path:
        v = self._raw.get("csv_path") or ""
        return Path(v).expanduser() if v else default_csv_path()

    @property
    def json_path(self) -> Path:
        v = self._raw.get("json_path") or ""
        return Path(v).expanduser() if v else default_json_path()

    @property
    def device_id(self) -> str:
        v = (self._raw.get("device_id") or "").strip()
        if v:
            return v
        return socket.gethostname().split(".")[0] or "default"

    @property
    def auto_clear_csv(self) -> bool:
        return bool(self._raw.get("auto_clear_csv", True))

    @property
    def clear_hour(self) -> int:
        return int(self._raw.get("clear_hour", 0))

    @property
    def clear_minute(self) -> int:
        return int(self._raw.get("clear_minute", 0))

    @property
    def clear_interval_days(self) -> int:
        """清理间隔（天）。1=每天，7=每周。最小 1。"""
        return max(1, int(self._raw.get("clear_interval_days", 1)))

    @property
    def enable_log(self) -> bool:
        """是否启用日志记录到 app.log。默认 True。"""
        return bool(self._raw.get("enable_log", True))

    @property
    def enable_plaintext(self) -> bool:
        """是否开启明文采集（CSV 第三列记录上屏原文）。默认 False。"""
        return bool(self._raw.get("enable_plaintext", False))


def load_config() -> _Config:
    cfg_path = config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg_path.exists():
        cfg_path.write_text(_DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
        raw: dict = {}
    else:
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[config] 读取失败，使用默认配置: {e}")
            raw = {}
    cfg = _Config(raw)
    for p in (cfg.csv_path, cfg.json_path):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"[config] 无法创建目录 {p.parent}: {e}")
    return cfg


def save_config(updates: dict) -> None:
    """把 updates 合并到 config.json。空字符串字段表示"用默认值"。"""
    cfg_path = config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if cfg_path.exists():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
    else:
        raw = json.loads(_DEFAULT_CONFIG_TEMPLATE)
    raw.update(updates)
    cfg_path.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def lua_path() -> Path:
    """返回 RIME 用户目录下的 words_counter.lua 路径。"""
    return rime_user_dir() / "lua" / "words_counter.lua"


def _bundled_lua_path() -> Optional[Path]:
    """返回 exe 同目录或源码目录下的 words_counter.lua。"""
    if is_frozen():
        p = Path(sys.executable).parent / "words_counter.lua"
        if p.exists():
            return p
    p = Path(__file__).parent / "words_counter.lua"
    if p.exists():
        return p
    return None


def sync_lua_plaintext(enable: bool) -> tuple[bool, str]:
    """将 RIME 用户目录的 words_counter.lua 中 ENABLE_PLAINTEXT 同步为 enable。

    若安装的是旧版（无 ENABLE_PLAINTEXT 行），自动用打包内的新版覆盖后再写入。
    """
    import re
    path = lua_path()
    new_val = "true" if enable else "false"

    def _patch(text: str) -> tuple[str, int]:
        return re.subn(
            r"^(local ENABLE_PLAINTEXT\s*=\s*)(true|false)(\s*--.*)?$",
            lambda m: f"{m.group(1)}{new_val}  -- true = 第三列记录上屏原文",
            text, count=1, flags=re.MULTILINE,
        )

    try:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            new_text, count = _patch(text)
            if count == 0:
                bundled = _bundled_lua_path()
                if bundled is None:
                    return False, "找不到内置新版 Lua 文件，无法自动升级"
                text = bundled.read_text(encoding="utf-8")
                new_text, count = _patch(text)
                if count == 0:
                    return False, "内置 Lua 文件中也未找到 ENABLE_PLAINTEXT，请联系开发者"
        else:
            bundled = _bundled_lua_path()
            if bundled is None:
                return False, f"未找到 Lua 文件且找不到内置版本：{path}"
            path.parent.mkdir(parents=True, exist_ok=True)
            text = bundled.read_text(encoding="utf-8")
            new_text, count = _patch(text)
            if count == 0:
                return False, "内置 Lua 文件中未找到 ENABLE_PLAINTEXT，请联系开发者"

        path.write_text(new_text, encoding="utf-8")
        return True, f"已将 ENABLE_PLAINTEXT 更新为 {new_val}"
    except OSError as e:
        return False, f"写入失败: {e}"


config = load_config()
CSV_FILE = str(config.csv_path)
JSON_FILE = str(config.json_path)
DEVICE_ID = config.device_id


# ================== 日志系统 ==================
_log_file = None  # 当前日志文件句柄
_orig_stdout = sys.stdout
_orig_stderr = sys.stderr


def _start_logging():
    """打开 app.log 并重定向 stdout/stderr，立即生效。"""
    global _log_file
    if _log_file is not None:
        return  # 已经在记录中
    log_path = app_support_dir() / "app.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _log_file = open(log_path, "a", encoding="utf-8", buffering=1)
        _log_file.write(f"\n{'='*60}\n")
        _log_file.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 日志开始\n")
        _log_file.write(f"{'='*60}\n")
        _log_file.flush()
        sys.stdout = _log_file
        sys.stderr = _log_file
        print(f"[log] 日志文件: {log_path}")
    except Exception as e:
        print(f"[warning] 日志初始化失败: {e}", file=_orig_stderr)


def _stop_logging():
    """停止日志记录，还原 stdout/stderr，关闭文件句柄。"""
    global _log_file
    if _log_file is None:
        return
    try:
        sys.stdout = _orig_stdout
        sys.stderr = _orig_stderr
        _log_file.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 日志停止\n")
        _log_file.flush()
        _log_file.close()
    except Exception:
        pass
    _log_file = None


if config.enable_log:
    _start_logging()


# ================== 主题 ==================
class Theme:
    """深色卡片风格的主题色板与排版常量。"""
    BG = "#1B1D23"
    SURFACE = "#23262F"
    SURFACE_HI = "#2E323D"
    BORDER = "#363B47"
    TEXT = "#ECEDEE"
    TEXT_DIM = "#8C92A3"
    ACCENT = "#7C9CFF"
    ACCENT_HI = "#A4BCFF"
    DANGER = "#FF6B6B"
    SUCCESS = "#4ADE80"
    WARN = "#FFB84D"
    HEAT_SCALE = ["#23262F", "#172E4E", "#173D70", "#1558A8", "#2488FF", "#7B24FF"]
    HEAT_THRESHOLDS = [0, 1999, 3999, 5999, 9999]   # 配合 HEAT_SCALE 的 1..5 档
    FONT_TITLE = ("Microsoft YaHei UI", 18, "bold")
    FONT_LABEL = ("Microsoft YaHei UI", 11)
    FONT_NUMBER = ("Microsoft YaHei UI", 28, "bold")
    FONT_SMALL = ("Microsoft YaHei UI", 10)


# ============================================================
#  数据层（原 wc_core.py 内联）
# ============================================================
SCHEMA_VERSION = 3


# -------- 文件锁上下文管理器 --------
@contextmanager
def safe_file(path: str, mode: str, retries: int = 3, delay: float = 0.1):
    """以 with 形式安全打开文件：自动重试 + 文件锁 + 退出时显式 unlock。

    只读模式拿共享锁 (LOCK_SH)，写入模式拿排他锁 (LOCK_EX)。
    """
    lock_type = portalocker.LOCK_SH if ("r" in mode and "+" not in mode) else portalocker.LOCK_EX
    last_err: Optional[Exception] = None
    f = None
    for attempt in range(retries):
        try:
            f = open(path, mode, encoding="utf-8")
            portalocker.lock(f, lock_type)
            break
        except (IOError, OSError, portalocker.LockException) as e:
            last_err = e
            if f is not None:
                try:
                    f.close()
                except Exception:
                    pass
                f = None
            if attempt == retries - 1:
                raise IOError(f"文件访问失败: {path} ({e})") from e
            time.sleep(delay)
    assert f is not None
    try:
        yield f
    finally:
        try:
            portalocker.unlock(f)
        except Exception:
            pass
        try:
            f.close()
        except Exception:
            pass


# -------- 事件回调总线 --------
class _CallbackBus:
    """简单的线程安全回调注册表，让 GUI / 托盘 / 悬浮窗订阅"数据更新"事件。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[dict], None]] = []

    def subscribe(self, cb: Callable[[dict], None]) -> None:
        with self._lock:
            if cb not in self._callbacks:
                self._callbacks.append(cb)

    def unsubscribe(self, cb: Callable[[dict], None]) -> None:
        with self._lock:
            if cb in self._callbacks:
                self._callbacks.remove(cb)

    def emit(self, payload: dict) -> None:
        with self._lock:
            cbs = list(self._callbacks)
        for cb in cbs:
            try:
                cb(payload)
            except Exception as e:
                print(f"[bus] 回调执行失败: {e}")


bus = _CallbackBus()


# -------- JSON schema helpers --------
def _empty_device_node() -> dict:
    return {"today_count": 0, "csv_offset": 0, "csv_inode": ""}


def _add_to_historical(state: dict, day_str: str, cnt: int) -> None:
    """把 cnt 字加进历史的 daily/monthly/yearly/total。"""
    state["daily"][day_str] = state["daily"].get(day_str, 0) + cnt
    state["monthly"][day_str[:7]] = state["monthly"].get(day_str[:7], 0) + cnt
    state["yearly"][day_str[:4]] = state["yearly"].get(day_str[:4], 0) + cnt
    state["total"] = state.get("total", 0) + cnt


def _flush_day(state: dict, day_str: str, device_id: str) -> None:
    """把当前设备当天的 today_count 汇合进历史，然后清零。

    每个设备只负责自己的跨天结算，避免跨设备竞争和重复计数。
    """
    node = state.get("devices", {}).get(device_id)
    if node is None:
        return
    cnt = int(node.get("today_count", 0))
    if cnt > 0:
        _add_to_historical(state, day_str, cnt)
        node["today_count"] = 0


def _migrate(raw: dict, device_id: str) -> dict:
    """把任意旧 schema 升级到 v3。"""
    if raw.get("schema") == SCHEMA_VERSION:
        raw.setdefault("devices", {}).setdefault(device_id, _empty_device_node())
        return raw

    state: dict = {
        "schema": SCHEMA_VERSION,
        "today": date.today().isoformat(),
        "daily": {}, "monthly": {}, "yearly": {}, "total": 0,
        "devices": {},
    }

    if raw.get("schema") == 2:
        # v2：各设备各有 daily/monthly/yearly/total，全部合并进历史
        for dev_name, dev_node in raw.get("devices", {}).items():
            for k, v in dev_node.get("daily", {}).items():
                state["daily"][k] = state["daily"].get(k, 0) + int(v)
            for k, v in dev_node.get("monthly", {}).items():
                state["monthly"][k] = state["monthly"].get(k, 0) + int(v)
            for k, v in dev_node.get("yearly", {}).items():
                state["yearly"][k] = state["yearly"].get(k, 0) + int(v)
            state["total"] += int(dev_node.get("total", 0))
            state["devices"][dev_name] = {
                "today_count": 0,
                "csv_offset": dev_node.get("csv_offset", 0),
                "csv_inode": dev_node.get("csv_inode", ""),
            }
    else:
        # v1 扁平格式
        state["daily"] = dict(raw.get("daily", {}))
        state["monthly"] = dict(raw.get("monthly", {}))
        state["yearly"] = dict(raw.get("yearly", {}))
        state["total"] = int(raw.get("total", 0))

    state["devices"].setdefault(device_id, _empty_device_node())
    return state


def _aggregate(state: dict) -> dict:
    """历史数据 + 今天各设备 today_count 加总 → 展示用的聚合视图。"""
    today_str = date.today().isoformat()
    # 只有 state["today"] 确实是今天，today_count 才计入今日
    # 否则说明 JSON 是旧的（尚未被 process_data flush），旧 today_count 不展示为今日
    if state.get("today") == today_str:
        today_total = sum(int(d.get("today_count", 0)) for d in state.get("devices", {}).values())
    else:
        today_total = 0

    daily = dict(state.get("daily", {}))
    monthly = dict(state.get("monthly", {}))
    yearly = dict(state.get("yearly", {}))
    total = int(state.get("total", 0))

    if today_total > 0:
        daily[today_str] = daily.get(today_str, 0) + today_total
        monthly[today_str[:7]] = monthly.get(today_str[:7], 0) + today_total
        yearly[today_str[:4]] = yearly.get(today_str[:4], 0) + today_total
        total += today_total

    return {"daily": daily, "monthly": monthly, "yearly": yearly, "total": total}


def _ensure_json(json_path: str, device_id: str) -> dict:
    """打开（或创建）JSON 文件，返回升级到 v3 的 state。"""
    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
    if not os.path.exists(json_path) or os.path.getsize(json_path) == 0:
        state = {
            "schema": SCHEMA_VERSION,
            "today": date.today().isoformat(),
            "daily": {}, "monthly": {}, "yearly": {}, "total": 0,
            "devices": {device_id: _empty_device_node()},
        }
        with safe_file(json_path, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        return state
    with safe_file(json_path, "r") as f:
        raw = json.load(f)
    state = _migrate(raw, device_id)
    if device_id not in state["devices"]:
        state["devices"][device_id] = _empty_device_node()
    return state


def _csv_signature(csv_path: str) -> str:
    """返回一个表示 CSV "身份" 的字符串，用于检测清空/重建。"""
    try:
        st = os.stat(csv_path)
        return f"{getattr(st, 'st_ino', 0)}-{int(st.st_ctime)}-{st.st_size}"
    except OSError:
        return ""


# -------- 数据处理 --------
class DataProcessor:
    """读取 CSV、累加到 JSON 的核心逻辑。线程安全。"""

    _process_lock = threading.Lock()  # 序列化 process_data，避免重入

    def __init__(self, csv_path: str, json_path: str, device_id: str):
        self.csv_path = csv_path
        self.json_path = json_path
        self.device_id = device_id

    def read_state(self) -> dict:
        return _ensure_json(self.json_path, self.device_id)

    def aggregate(self) -> dict:
        return _aggregate(self.read_state())

    def process_data(self) -> dict:
        with self._process_lock:
            state = _ensure_json(self.json_path, self.device_id)

            # 日期翻转检测：若存储的 today != 实际今天，先 flush 昨天的计数
            stored_today = state.get("today", "")
            actual_today = date.today().isoformat()
            date_changed = bool(stored_today) and stored_today != actual_today
            if date_changed:
                _flush_day(state, stored_today, self.device_id)
                state["today"] = actual_today
            elif not stored_today:
                state["today"] = actual_today

            node = state["devices"].setdefault(self.device_id, _empty_device_node())

            new_signature = _csv_signature(self.csv_path)
            stored_signature = node.get("csv_inode", "")
            signature_changed = bool(new_signature) and new_signature != stored_signature

            if signature_changed:
                try:
                    if os.path.getsize(self.csv_path) < node.get("csv_offset", 0):
                        node["csv_offset"] = 0
                except OSError:
                    pass
                node["csv_inode"] = new_signature

            new_entries = self._read_new_rows(node)
            if new_entries:
                self._apply_entries(state, node, new_entries)
            if new_entries or signature_changed or date_changed:
                self._write_state(state, date_changed=date_changed)
            agg = _aggregate(state)
            if new_entries:
                bus.emit(agg)
            return agg

    def _read_new_rows(self, node: dict) -> list[tuple[datetime, int]]:
        if not os.path.exists(self.csv_path):
            return []
        entries: list[tuple[datetime, int]] = []
        with safe_file(self.csv_path, "r") as f:
            offset = int(node.get("csv_offset", 0))
            if offset == 0:
                header = f.readline()
                if not header:
                    node["csv_offset"] = f.tell()
                    return []
                # 兼容两列（普通版）和三列（明文版），以及旧格式的引号包裹
                head_cols = header.strip().replace('"', "").split(",")
                if len(head_cols) < 2 or head_cols[:2] != ["timestamp", "chinese_count"]:
                    return []
                offset = f.tell()
            else:
                f.seek(offset)

            for line in f:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                parts = next(csv.reader([line]))
                if len(parts) < 2:
                    continue
                try:
                    ts = datetime.fromisoformat(parts[0])
                    cnt = int(parts[1])
                except (ValueError, TypeError):
                    continue
                entries.append((ts, cnt))
            node["csv_offset"] = f.tell()
        return entries

    def _apply_entries(self, state: dict, node: dict, entries: list[tuple[datetime, int]]) -> None:
        """今天的字 → today_count；过去日期的字（app 跨夜未开）→ 历史。"""
        today_str = state.get("today", "")
        for ts, cnt in entries:
            day = ts.strftime("%Y-%m-%d")
            if day == today_str:
                node["today_count"] = node.get("today_count", 0) + cnt
            else:
                _add_to_historical(state, day, cnt)

    def _write_state(self, state: dict, date_changed: bool = False) -> None:
        """读-改-写，整体上排他锁包住，避免和其他设备同时写时丢数据。"""
        with safe_file(self.json_path, "r+") as f:
            try:
                on_disk = json.load(f)
            except json.JSONDecodeError:
                on_disk = {}
            merged = self._merge_states(on_disk, state, owner=self.device_id, date_changed=date_changed)
            f.seek(0)
            json.dump(merged, f, indent=2, ensure_ascii=False)
            f.truncate()

    @staticmethod
    def _merge_states(on_disk: dict, ours: dict, owner: str, date_changed: bool = False) -> dict:
        """v3 合并：历史数据取 max（CRDT 安全），自己设备节点用内存版，其他设备节点用磁盘版。"""
        if on_disk.get("schema") != SCHEMA_VERSION:
            on_disk = _migrate(on_disk, owner)
        result = {
            "schema": SCHEMA_VERSION,
            "today": ours.get("today", on_disk.get("today", "")),
            "daily": dict(on_disk.get("daily", {})),
            "monthly": dict(on_disk.get("monthly", {})),
            "yearly": dict(on_disk.get("yearly", {})),
            "total": int(on_disk.get("total", 0)),
            "devices": dict(on_disk.get("devices", {})),
        }
        # 历史数据：ours 已经在 on_disk 基础上累加，直接用 max 安全合并
        for key in ("daily", "monthly", "yearly"):
            for k, v in ours.get(key, {}).items():
                result[key][k] = max(result[key].get(k, 0), int(v))
        result["total"] = max(result["total"], int(ours.get("total", 0)))
        # 跨天且磁盘还是旧日期：顺手把其他设备的 today_count 也结算进历史并清零
        if date_changed and on_disk.get("today") != ours.get("today"):
            disk_today = on_disk.get("today", "")
            if disk_today:
                for dev_id, dev_node in result["devices"].items():
                    if dev_id == owner:
                        continue
                    cnt = int(dev_node.get("today_count", 0))
                    if cnt > 0:
                        _add_to_historical(result, disk_today, cnt)
                        dev_node["today_count"] = 0
        # 自己的设备节点用内存版本
        my_node = ours.get("devices", {}).get(owner)
        if my_node is not None:
            result["devices"][owner] = my_node
        return result

    def clear_csv(self) -> bool:
        try:
            with safe_file(self.csv_path, "w") as f:
                f.write("timestamp,chinese_count,text\n")
            with safe_file(self.json_path, "r+") as f:
                state = json.load(f)
                if state.get("schema") != SCHEMA_VERSION:
                    state = _migrate(state, self.device_id)
                node = state["devices"].setdefault(self.device_id, _empty_device_node())
                node["csv_offset"] = 0
                node["csv_inode"] = _csv_signature(self.csv_path)
                f.seek(0)
                json.dump(state, f, indent=2, ensure_ascii=False)
                f.truncate()
            bus.emit(_aggregate(state))
            return True
        except Exception as e:
            print(f"[clear_csv] 失败: {e}")
            return False


def schedule_daily(callback: Callable[[], None], hour: int = 0, minute: int = 0,
                    interval_days: int = 1) -> "_DailyScheduler":
    """每隔 interval_days 天的 hour:minute 执行 callback，返回可取消的 scheduler。"""
    return _DailyScheduler(callback, hour, minute, interval_days)


def _rename_device_in_json(json_path: str, old_id: str, new_id: str) -> None:
    """把历史 JSON 里的设备节点从 old_id 重命名为 new_id，避免改名后双重计数。"""
    if old_id == new_id:
        return
    try:
        with safe_file(json_path, "r+") as f:
            state = json.load(f)
            devices = state.get("devices", {})
            if old_id not in devices:
                return
            if new_id not in devices:
                devices[new_id] = devices.pop(old_id)
            else:
                del devices[old_id]
            state["devices"] = devices
            f.seek(0)
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.truncate()
    except Exception as e:
        print(f"[rename_device] 设备节点重命名失败: {e}")


def _last_clear_state_file() -> Path:
    return app_support_dir() / ".last_clear_time"


def _read_last_clear_time() -> Optional[datetime]:
    p = _last_clear_state_file()
    if not p.exists():
        return None
    try:
        return datetime.fromisoformat(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _write_last_clear_time(t: datetime) -> None:
    p = _last_clear_state_file()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(t.isoformat(), encoding="utf-8")
    except OSError as e:
        print(f"[scheduler] 写入清理时间戳失败: {e}")


class _DailyScheduler:
    """可取消的间隔定时任务：每隔 interval_days 天的 hour:minute 执行 callback。"""
    def __init__(self, callback: Callable[[], None], hour: int, minute: int,
                 interval_days: int = 1):
        self._stop_event = threading.Event()
        self._callback = callback
        self._hour = hour
        self._minute = minute
        self._interval_days = max(1, interval_days)
        self._thread = threading.Thread(target=self._runner, daemon=True,
                                         name="wc_daily_scheduler")
        self._thread.start()

    def _next_target(self) -> datetime:
        now = datetime.now()
        last = _read_last_clear_time()
        if last is not None:
            base_date = last.date() + timedelta(days=self._interval_days)
            target = datetime.combine(base_date, datetime.min.time()).replace(
                hour=self._hour, minute=self._minute, second=0, microsecond=0)
        else:
            target = now.replace(hour=self._hour, minute=self._minute,
                                  second=0, microsecond=0)
            # 如果目标时间已过（含当前分钟），推到下个周期
            if target < now:
                target = target + timedelta(days=self._interval_days)
        # 防止边界情况导致 target 仍在过去
        while target < now:
            target = target + timedelta(days=self._interval_days)
        return target

    def _runner(self):
        while not self._stop_event.is_set():
            target = self._next_target()
            wait_s = max(1.0, (target - datetime.now()).total_seconds())
            print(f"[scheduler] 下次清理: {target.strftime('%Y-%m-%d %H:%M:%S')} "
                  f"(间隔 {self._interval_days} 天)")
            if self._stop_event.wait(timeout=wait_s):
                break
            try:
                self._callback()
                _write_last_clear_time(datetime.now())
            except Exception as e:
                print(f"[scheduler] 定时任务执行失败: {e}")

    def stop(self):
        self._stop_event.set()


# 全局数据处理器（GUI、托盘、悬浮窗共用一个实例）
processor = DataProcessor(CSV_FILE, JSON_FILE, DEVICE_ID)


# ============================================================
#  UI / 平台特定逻辑
# ============================================================

# -------- 防抖 --------
class Debouncer:
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


# -------- 文件监控 --------
class CSVHandler(PatternMatchingEventHandler):
    def __init__(self, on_change):
        super().__init__(patterns=[CSV_FILE], ignore_directories=True)
        self._debounce = Debouncer(0.5, on_change)

    def on_modified(self, event):
        self._debounce.trigger()

    def on_created(self, event):
        self._debounce.trigger()


# -------- 测速 --------
class SpeedTester:
    """以本设备 CSV 增量字数为口径，避免多设备同步时 total 数据污染。"""

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
                next(reader, None)  # header
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


# -------- 系统托盘 --------
class SysTrayManager:
    def __init__(self, master):
        self.master = master
        self.icon: pystray.Icon | None = None
        self._build()

    def _build(self):
        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        dc = ImageDraw.Draw(image)
        dc.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=15,
                             fill="#1660f5", outline=None, width=0)
        try:
            font = ImageFont.truetype("msyh.ttc", 48)
        except IOError:
            font = ImageFont.load_default()
        text = "字"
        bbox = dc.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        dc.text(((size - tw) // 2, (size - th) // 2 - 10), text, fill="white", font=font)

        menu = pystray.Menu(
            pystray.MenuItem("显示主界面", self._show_main),
            pystray.MenuItem("切换悬浮窗", self._toggle_taskbar),
            pystray.MenuItem("设置…", self._open_settings),
            pystray.MenuItem("配置文件夹与日志", self._open_config_folder),
            pystray.MenuItem("手动清理CSV", self._clear_csv),
            pystray.MenuItem("GitHub", self._open_github),
            pystray.MenuItem("退出", self._exit_app),
        )
        self.icon = pystray.Icon("word_counter", image, "字数统计工具 by hyuan", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def _show_main(self, _icon, _item):
        self.master.after(0, self.master.show_window)

    def _toggle_taskbar(self, _icon, _item):
        self.master.after(0, self.master.taskbar.toggle)

    def _open_settings(self, _icon, _item):
        # 设置窗口现在是独立窗口，不需要显示主窗口
        self.master.after(0, lambda: SettingsWindow(self.master))

    def _open_config_folder(self, _icon, _item):
        folder = config_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        # 高亮选中 app.log，方便用户直接看到日志文件
        log_file = folder / "app.log"
        if log_file.exists():
            subprocess.Popen(["explorer", f"/select,{log_file}"])
        else:
            os.startfile(str(folder))

    def _open_github(self, _icon, _item):
        import webbrowser
        webbrowser.open("https://github.com/hyuan42/Rime-words-counter")

    def _clear_csv(self, _icon, _item):
        def do():
            try:
                processor.process_data()
                ok = processor.clear_csv()
                if ok:
                    messagebox.showinfo("成功", "CSV文件已清空！")
                else:
                    messagebox.showerror("错误", "CSV 清空失败，详见日志")
            except Exception as e:
                messagebox.showerror("错误", f"清空失败: {e}")
        self.master.after(0, do)

    def _exit_app(self, _icon, _item):
        if self.icon is not None:
            self.icon.stop()
        self.master.after(0, self.master.full_exit)


# -------- 悬浮窗（Win32） --------
class TaskbarWindow:
    def __init__(self, master):
        self.master = master
        self.hwnd = None
        self.running = True
        self.visible = True
        self._create_window()
        self._start_message_loop()
        bus.subscribe(self._on_data_update)
        self._refresh()

    def _on_data_update(self, _payload):
        self._refresh()

    def _refresh(self):
        try:
            agg = processor.aggregate()
            today = agg["daily"].get(datetime.now().strftime("%Y-%m-%d"), 0)
            if self.hwnd:
                win32gui.SetWindowText(self.hwnd, f"今日字数：{today}字")
        except Exception as e:
            print(f"[taskbar] 更新失败: {e}")

    def _start_message_loop(self):
        def loop():
            while self.running:
                win32gui.PumpWaitingMessages()
                time.sleep(0.1)
        threading.Thread(target=loop, daemon=True).start()

    def _wnd_proc(self, hwnd, msg, wParam, lParam):
        if msg == win32con.WM_CLOSE:
            self.hide()
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wParam, lParam)

    def _create_window(self):
        wnd_class = win32gui.WNDCLASS()
        wnd_class.lpszClassName = "TaskbarCounter"
        wnd_class.hInstance = win32api.GetModuleHandle(None)
        wnd_class.lpfnWndProc = self._wnd_proc
        self.class_atom = win32gui.RegisterClass(wnd_class)

        ex_style = win32con.WS_EX_LAYERED | win32con.WS_EX_TOOLWINDOW
        style = win32con.WS_OVERLAPPED | win32con.WS_SYSMENU
        self.hwnd = win32gui.CreateWindowEx(
            ex_style, self.class_atom, "今日字数：初始化...", style,
            0, 0, 100, 100, 0, 0, wnd_class.hInstance, None,
        )
        sw = win32api.GetSystemMetrics(0)
        sh = win32api.GetSystemMetrics(1)
        win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST,
                              sw - 200, sh - 100, 180, 40, win32con.SWP_SHOWWINDOW)
        win32gui.SetLayeredWindowAttributes(self.hwnd, 0, 255, win32con.LWA_ALPHA)

    def hide(self):
        if self.hwnd:
            win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)
            self.visible = False

    def show(self):
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            self._create_window()
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOW)
        self.visible = True

    def toggle(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    def close(self):
        self.running = False
        bus.unsubscribe(self._on_data_update)
        if self.hwnd:
            try:
                win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass


# -------- 历史记录窗口 --------
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
        self.geometry(f"{win_w}x624")
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
            font=("Microsoft YaHei UI", 12, "bold"),
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
        self.line_canvas.bind("<Button-1>", self._on_line_click)
        self.line_canvas.bind("<Configure>", self._on_line_configure)
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
            font=("Microsoft YaHei UI", 16, "bold"),
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
                    # 未来日期：灰色，无数据
                    fill = Theme.HEAT_SCALE[0]
                    payload = (key, None)
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
        """折线图：本年 12 个月，每月一个点。"""
        c = self.line_canvas
        c.delete("all")
        self._line_lookup.clear()

        monthly = agg.get("monthly", {})
        months = []
        for m in range(1, 13):
            key = f"{year}-{m:02d}"
            months.append((key, m, int(monthly.get(key, 0))))

        c.update_idletasks()
        W = c.winfo_width() or int(c["width"])
        H = c.winfo_height() or int(c["height"])
        if W < 200 or H < 80:
            return
        PAD_L, PAD_R, PAD_T, PAD_B = 44, 16, 14, 28

        max_v = max((v for _, _, v in months), default=0)
        max_y = max(1, int(max_v * 1.1)) if max_v > 0 else 1

        plot_w = W - PAD_L - PAD_R
        plot_h = H - PAD_T - PAD_B

        def x_at(m_idx: int) -> float:
            return PAD_L + plot_w * m_idx / 11

        def y_at(v: int) -> float:
            return PAD_T + plot_h - (v / max_y) * plot_h

        # y 轴参考线
        for i in range(5):
            y = PAD_T + plot_h * i / 4
            c.create_line(PAD_L, y, W - PAD_R, y,
                           fill=Theme.BORDER, width=1, dash=(2, 4))
            v = int(max_y * (4 - i) / 4)
            c.create_text(PAD_L - 6, y, text=f"{v:,}", anchor="e",
                           fill=Theme.TEXT_DIM, font=Theme.FONT_SMALL)

        # x 轴月份标签
        for idx, (_key, m, _v) in enumerate(months):
            x = x_at(idx)
            c.create_text(x, H - PAD_B + 12, text=f"{m}月",
                           fill=Theme.TEXT_DIM, font=Theme.FONT_SMALL)

        # 当前月份索引（非当前年则全部显示）
        today = date.today()
        current_month_idx = today.month - 1 if year == today.year else None
        last_idx = current_month_idx if current_month_idx is not None else 11

        # 连线：只连到当前月，未来不画
        if max_v > 0:
            pts = []
            for idx, (_key, _m, v) in enumerate(months):
                if idx > last_idx:
                    break
                pts.extend([x_at(idx), y_at(v)])
            if len(pts) >= 4:
                c.create_line(*pts, fill=Theme.ACCENT, width=2)

        # 节点 + 标签
        for idx, (key, _m, v) in enumerate(months):
            if idx > last_idx:
                continue
            x = x_at(idx)
            y = y_at(v)

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
                 font=("Microsoft YaHei UI", 20, "bold")).pack(pady=(0, 2))
        # 字数：居中、小字
        tk.Label(inner, text=count_text, bg=bg, fg=count_fg,
                 font=("Microsoft YaHei UI", 11)).pack()
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


# ================== 设置窗口 ==================
class SettingsWindow(tk.Toplevel):
    """配置编辑器：浏览路径 / 修改设备名 / 设置自动清空时间。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("设置")
        self.configure(bg=Theme.BG)
        self.geometry("560x620")
        self.resizable(False, False)
        # 不设置 transient 和 grab_set，让设置窗口可以独立于主窗口显示

        _load_cfg = load_config
        _save_cfg = save_config
        self._save_config = _save_cfg
        self._sync_lua = sync_lua_plaintext
        self._cfg = _load_cfg()
        self._default_csv = str(default_csv_path())
        self._default_json = str(default_json_path())

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
        tk.Label(self, text="⚙ 设置", bg=Theme.BG, fg=Theme.TEXT,
                 font=Theme.FONT_TITLE).pack(anchor="w", padx=24, pady=(20, 14))

        bar = tk.Frame(self, bg=Theme.BG)
        bar.pack(side="bottom", fill="x", padx=24, pady=18)
        cancel = self._action_button(bar, "取消", self.destroy, primary=False)
        cancel.pack(side="right")
        save = self._action_button(bar, "保存", self._on_save, primary=True)
        save.pack(side="right", padx=(0, 8))
        tk.Frame(self, bg=Theme.BORDER, height=1).pack(
            side="bottom", fill="x", padx=24, pady=(0, 0))

        # Tab 容器
        tab_container = tk.Frame(self, bg=Theme.BG)
        tab_container.pack(fill="both", expand=True, padx=24, pady=(0, 12))

        tab_bar = tk.Frame(tab_container, bg=Theme.BG)
        tab_bar.pack(fill="x", pady=(0, 12))
        self._tab_buttons = []
        for idx, name in enumerate(["基础设置", "其他"]):
            btn = tk.Label(
                tab_bar, text=name, bg=Theme.SURFACE, fg=Theme.TEXT,
                font=Theme.FONT_LABEL, padx=16, pady=6, cursor="hand2")
            btn.pack(side="left", padx=(0, 6))
            btn.bind("<Button-1>", lambda e, i=idx: self._switch_tab(i))
            self._tab_buttons.append(btn)

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
        self._field_count = 0
        self._scrollable = self._tab_frames[1]
        self._build_clear_row()
        self._build_log_row()
        self._build_plaintext_row()

        self._switch_tab(0)

    def _switch_tab(self, idx: int):
        self._current_tab = idx
        for i, btn in enumerate(self._tab_buttons):
            if i == idx:
                btn.config(bg=Theme.SURFACE_HI, fg=Theme.TEXT)
            else:
                btn.config(bg=Theme.SURFACE, fg=Theme.TEXT_DIM)
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
                 font=("Consolas", 11), anchor="w", padx=10, pady=8).pack(fill="x")

    def _build_json_row(self):
        wrap = self._field_wrap(
            "历史 JSON 文件路径",
            "字数累计数据存放位置。多设备同步建议放云盘（iCloud/OneDrive 等）。"
            "留空 = 默认放应用配置目录。",
        )
        row = tk.Frame(wrap, bg=Theme.BG)
        row.pack(fill="x", pady=(6, 0))

        entry_frame = tk.Frame(row, bg=Theme.SURFACE,
                                highlightthickness=1, highlightbackground=Theme.BORDER)
        entry_frame.pack(side="left", fill="x", expand=True)
        self.entry_json = tk.Entry(
            entry_frame, textvariable=self.var_json,
            bg=Theme.SURFACE, fg=Theme.TEXT, insertbackground=Theme.TEXT,
            relief="flat", bd=0, font=("Consolas", 11), highlightthickness=0,
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
            relief="flat", bd=0, font=("Microsoft YaHei UI", 12), highlightthickness=0,
        ).pack(fill="x", padx=10, pady=8)
        # 提示当前生效设备名
        tk.Label(wrap, text=f"当前生效: {self._cfg.device_id}",
                 bg=Theme.BG, fg=Theme.TEXT_DIM,
                 font=Theme.FONT_SMALL).pack(anchor="w", pady=(4, 0))

    def _make_switch(self, parent, var: tk.BooleanVar, on_toggle=None):
        """iOS 风格开关（Canvas 实现）。"""
        W, H = 46, 26
        R = H // 2
        canvas = tk.Canvas(parent, width=W, height=H, bg=Theme.BG,
                           highlightthickness=0, bd=0, cursor="hand2")

        def redraw():
            canvas.delete("all")
            on = bool(var.get())
            track = Theme.ACCENT if on else Theme.BORDER
            canvas.create_oval(1, 1, H - 1, H - 1, fill=track, outline=track)
            canvas.create_oval(W - H + 1, 1, W - 1, H - 1, fill=track, outline=track)
            canvas.create_rectangle(R, 1, W - R, H - 1, fill=track, outline=track)
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
        return canvas

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

        self._clear_detail = tk.Frame(wrap, bg=Theme.BG)

        time_row = tk.Frame(self._clear_detail, bg=Theme.BG)
        time_row.pack(fill="x", pady=(10, 0))
        tk.Label(time_row, text="每隔", bg=Theme.BG, fg=Theme.TEXT_DIM,
                 font=Theme.FONT_LABEL).pack(side="left")

        days_frame = tk.Frame(time_row, bg=Theme.SURFACE,
                               highlightthickness=1, highlightbackground=Theme.BORDER)
        days_frame.pack(side="left", padx=(6, 0))
        tk.Spinbox(
            days_frame, from_=1, to=365, textvariable=self.var_interval_days, width=4,
            bg=Theme.SURFACE, fg=Theme.TEXT, insertbackground=Theme.TEXT,
            buttonbackground=Theme.SURFACE_HI,
            relief="flat", bd=0, font=("Microsoft YaHei UI", 12),
            highlightthickness=0, justify="center",
        ).pack(padx=4, pady=4)
        tk.Label(time_row, text="天的", bg=Theme.BG, fg=Theme.TEXT_DIM,
                 font=Theme.FONT_LABEL).pack(side="left", padx=(4, 0))

        for var, suffix, max_v in ((self.var_hour, "时", 23), (self.var_minute, "分", 59)):
            spin_frame = tk.Frame(time_row, bg=Theme.SURFACE,
                                   highlightthickness=1, highlightbackground=Theme.BORDER)
            spin_frame.pack(side="left", padx=(6, 0))
            tk.Spinbox(
                spin_frame, from_=0, to=max_v, textvariable=var, width=4,
                bg=Theme.SURFACE, fg=Theme.TEXT, insertbackground=Theme.TEXT,
                buttonbackground=Theme.SURFACE_HI,
                relief="flat", bd=0, font=("Microsoft YaHei UI", 12),
                highlightthickness=0, justify="center",
            ).pack(padx=4, pady=4)
            tk.Label(time_row, text=suffix, bg=Theme.BG, fg=Theme.TEXT_DIM,
                     font=Theme.FONT_LABEL).pack(side="left", padx=(4, 0))

        tk.Label(self._clear_detail, text="例如：1=每天清空，7=每周清空。",
                 bg=Theme.BG, fg=Theme.TEXT_DIM,
                 font=Theme.FONT_SMALL).pack(anchor="w", pady=(6, 0))

        self._toggle_clear_visibility()

    def _toggle_clear_visibility(self):
        if self.var_auto_clear.get():
            self._clear_detail.pack(fill="x")
        else:
            self._clear_detail.pack_forget()

    def _build_log_row(self):
        self._field_wrap(
            "启用日志",
            "记录运行日志到 app.log（位于配置文件夹）。出问题时可以提供给开发者排查。",
            right_widget_factory=lambda p: self._make_switch(p, self.var_enable_log),
        )

    def _build_plaintext_row(self):
        self._field_wrap(
            "明文版",
            "⭐开启后 CSV 第三列记录上屏原文。每次切换后，必须执行“重新部署”rime输入法才能生效",
            right_widget_factory=lambda p: self._make_switch(p, self.var_plaintext),
        )

    def _field_wrap(self, title: str, hint: str, right_widget_factory=None) -> tk.Frame:
        """right_widget_factory: callable(parent) -> widget，放在标题行右侧。"""
        parent = getattr(self, '_scrollable', self)

        if getattr(self, '_field_count', 0) > 0:
            tk.Frame(parent, bg=Theme.BORDER, height=1).pack(
                fill="x", padx=0, pady=(0, 14))
        self._field_count = getattr(self, '_field_count', 0) + 1

        wrap = tk.Frame(parent, bg=Theme.BG)
        wrap.pack(fill="x", padx=0, pady=(0, 14))

        title_row = tk.Frame(wrap, bg=Theme.BG)
        title_row.pack(fill="x")
        tk.Label(title_row, text=title, bg=Theme.BG, fg=Theme.TEXT,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
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
                          font=("Microsoft YaHei UI", 12, "bold"),
                          cursor="hand2", padx=22, pady=10)
        label.pack()
        for w in (frame, label):
            w.bind("<Enter>", lambda _e: (frame.config(bg=active_bg),
                                            label.config(bg=active_bg)))
            w.bind("<Leave>", lambda _e: (frame.config(bg=bg),
                                            label.config(bg=bg)))
            w.bind("<Button-1>", lambda _e: cmd())
        return frame

    def _small_button(self, parent, text: str, cmd) -> tk.Frame:
        frame = tk.Frame(parent, bg=Theme.SURFACE)
        label = tk.Label(frame, text=text, bg=Theme.SURFACE, fg=Theme.TEXT,
                          font=("Microsoft YaHei UI", 11),
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
        try:
            hour = int(self.var_hour.get())
            minute = int(self.var_minute.get())
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "清空时间必须是有效的小时（0-23）和分钟（0-59）",
                                  parent=self)
            return

        try:
            interval_days = max(1, int(self.var_interval_days.get()))
        except ValueError:
            interval_days = 1

        new_json = self.var_json.get().strip()
        old_json_path = self._cfg.json_path

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

        # 记录旧值，用于判断哪些设置发生了变化
        old_device_id = self._cfg.device_id
        old_enable_log = self._cfg.enable_log
        old_enable_plaintext = self._cfg.enable_plaintext
        new_enable_log = bool(self.var_enable_log.get())
        new_enable_plaintext = bool(self.var_plaintext.get())

        try:
            self._save_config({
                "json_path": new_json,
                "device_id": self.var_device.get().strip(),
                "auto_clear_csv": bool(self.var_auto_clear.get()),
                "clear_hour": hour,
                "clear_minute": minute,
                "clear_interval_days": interval_days,
                "enable_log": new_enable_log,
                "enable_plaintext": new_enable_plaintext,
            })
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}", parent=self)
            return

        # 设备名 / JSON 路径变更：热更新 processor（用锁防止轮询线程在更新期间插入）
        new_cfg = load_config()
        new_device_id = new_cfg.device_id
        new_json_path_str = str(new_cfg.json_path)
        device_changed = old_device_id != new_device_id
        json_path_changed = new_json_path_str != processor.json_path

        if device_changed or json_path_changed:
            with DataProcessor._process_lock:
                if device_changed:
                    # 设备改名在新路径的 JSON 上执行（路径同时改变时新路径才是后续操作目标）
                    _rename_device_in_json(
                        new_json_path_str if json_path_changed else processor.json_path,
                        old_device_id, new_device_id,
                    )
                    processor.device_id = new_device_id
                if json_path_changed:
                    processor.json_path = new_json_path_str

        # 日志热重载：立即生效，不需要重启
        if new_enable_log != old_enable_log:
            if new_enable_log:
                _start_logging()
            else:
                _stop_logging()

        # 明文版：同步 Lua 文件
        if new_enable_plaintext != old_enable_plaintext:
            ok, msg = self._sync_lua(new_enable_plaintext)
            if not ok:
                messagebox.showwarning(
                    "Lua 同步提示",
                    f"设置已保存，但自动同步 Lua 文件失败：\n{msg}\n\n"
                    "请手动修改 words_counter.lua 中的 ENABLE_PLAINTEXT 值。",
                    parent=self,
                )

        messagebox.showinfo("已保存", "设置已保存。", parent=self)

        # 调度器热重载：新的清理时间立即生效，不需要重启应用
        try:
            app = self.master
            if hasattr(app, '_start_scheduler'):
                app._start_scheduler()
        except Exception as e:
            print(f"[settings] 调度器重载失败: {e}")

        self.destroy()


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("字数统计")
        self.configure(bg=Theme.BG)
        self.geometry("520x560")
        self.minsize(520, 560)
        self.withdraw()
        self.protocol("WM_DELETE_WINDOW", self.hide_window)

        # 启动时把积压的 CSV 处理一次
        processor.process_data()

        self.taskbar = TaskbarWindow(self)
        self.tray = SysTrayManager(self)

        self._observers: list[Observer] = []
        self._scheduler = None
        self.speed_tester = SpeedTester()
        self._speed_after_id = None
        self._stat_values: dict[str, tk.Label] = {}

        self._build_ui()
        self._refresh_display()

        bus.subscribe(self._on_data_update)
        self._start_observer()
        self._start_scheduler()

    def _build_ui(self):
        # 顶部标题
        header = tk.Frame(self, bg=Theme.BG)
        header.pack(fill="x", padx=24, pady=(22, 18))
        tk.Label(header, text="📝", bg=Theme.BG, fg=Theme.ACCENT,
                 font=("Microsoft YaHei UI", 22)).pack(side="left", padx=(0, 8))
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
                                   fg=Theme.TEXT_DIM, font=("Microsoft YaHei UI", 12))
        self._speed_dot.pack(side="left")
        tk.Label(head, text="  输入速度", bg=Theme.SURFACE, fg=Theme.TEXT_DIM,
                 font=Theme.FONT_LABEL).pack(side="left")
        self._speed_value = tk.Label(
            head, text="未测速", bg=Theme.SURFACE, fg=Theme.TEXT,
            font=("Microsoft YaHei UI", 14, "bold"),
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
        bg = Theme.ACCENT if primary else Theme.SURFACE
        fg = "#0F1218" if primary else Theme.TEXT
        active_bg = Theme.ACCENT_HI if primary else Theme.SURFACE_HI
        btn = tk.Button(
            parent, text=text, command=cmd,
            bg=bg, fg=fg,
            activebackground=active_bg, activeforeground=fg,
            relief="flat", bd=0, height=2,
            font=("Microsoft YaHei UI", 12, "bold"),
            highlightthickness=0, cursor="hand2",
        )
        def on_enter(e):
            btn.config(bg=active_bg)
        def on_leave(e):
            btn.config(bg=bg)
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        return btn

    def _refresh_display(self):
        try:
            agg = processor.aggregate()
        except Exception as e:
            print(f"[ui] 读取聚合失败: {e}")
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
        self.after(0, self._refresh_display)

    def _toggle_speed(self):
        if not self.speed_tester.active:
            self.speed_tester.start()
            self.btn_speed.config(text="结束测速", bg=Theme.DANGER, fg="#0F1218")
            self.btn_speed.bind("<Enter>", lambda _e: self.btn_speed.config(bg="#FF8585"))
            self.btn_speed.bind("<Leave>", lambda _e: self.btn_speed.config(bg=Theme.DANGER))
            self._speed_dot.config(fg=Theme.ACCENT)
            self._speed_value.config(text="测速中…", fg=Theme.ACCENT)
            self._tick_speed()
        else:
            self.speed_tester.stop()
            self.btn_speed.config(text="开始测速", bg=Theme.ACCENT, fg="#0F1218")
            self.btn_speed.bind("<Enter>", lambda _e: self.btn_speed.config(bg=Theme.ACCENT_HI))
            self.btn_speed.bind("<Leave>", lambda _e: self.btn_speed.config(bg=Theme.ACCENT))
            self._speed_dot.config(fg=Theme.TEXT_DIM)
            self._speed_value.config(
                text=self.speed_tester.last_speed_label, fg=Theme.TEXT,
            )
            if self._speed_after_id is not None:
                self.after_cancel(self._speed_after_id)
                self._speed_after_id = None

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

    def _start_observer(self):
        csv_dir = os.path.dirname(CSV_FILE)
        # ディレクトリが無ければ作っておく（Lua がまだ一度も起動していない場合）
        os.makedirs(csv_dir, exist_ok=True)

        observer = Observer()
        observer.schedule(CSVHandler(processor.process_data),
                          csv_dir, recursive=False)
        try:
            observer.start()
            self._observers.append(observer)
            print(f"[watchdog] 监听 CSV 目录: {csv_dir}")
        except Exception as e:
            print(f"[watchdog] 启动失败: {e}")

        # 后台轮询兜底（每 1.5 秒）：watchdog 偶尔会被杀软或网络盘卡住
        self._poller_running = True

        def poll_loop():
            while self._poller_running:
                try:
                    processor.process_data()
                except Exception as e:
                    print(f"[poller] CSV 处理失败: {e}")
                time.sleep(1.5)

        t = threading.Thread(target=poll_loop, daemon=True, name="csv-poller")
        t.start()

    def _start_scheduler(self):
        if hasattr(self, '_scheduler') and self._scheduler is not None:
            self._scheduler.stop()
            self._scheduler = None

        cfg = load_config()
        if not cfg.auto_clear_csv:
            print("[scheduler] CSV 定时清理未启用")
            return

        def clear_csv_daily():
            try:
                processor.process_data()
                processor.clear_csv()
                print(f"[{datetime.now()}] CSV 已清空")
            except Exception as e:
                print(f"[scheduler] 清空失败: {e}")

        self._scheduler = schedule_daily(
            clear_csv_daily,
            hour=cfg.clear_hour,
            minute=cfg.clear_minute,
            interval_days=cfg.clear_interval_days,
        )

    def show_window(self):
        self.deiconify()
        self.lift()
        self.attributes("-topmost", 1)
        self.after(100, lambda: self.attributes("-topmost", 0))

    def hide_window(self):
        self.withdraw()

    def full_exit(self):
        try:
            self._poller_running = False  # 停止后台轮询
            bus.unsubscribe(self._on_data_update)
            if hasattr(self, "taskbar"):
                self.taskbar.close()
            for obs in self._observers:
                try:
                    obs.stop()
                    obs.join(timeout=1)
                except Exception as e:
                    print(f"[exit] observer 关闭失败: {e}")
        except Exception as e:
            print(f"[exit] {e}")
        finally:
            try:
                self.destroy()
            except Exception:
                pass
            sys.exit(0)


if __name__ == "__main__":
    if "--clear-csv" in sys.argv:
        processor.process_data()
        ok = processor.clear_csv()
        sys.exit(0 if ok else 1)
    Application().mainloop()
