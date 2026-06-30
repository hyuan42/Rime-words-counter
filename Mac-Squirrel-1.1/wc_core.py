"""
版本: 字数统计工具-鼠须管-共享数据层 (wc_core.py) 1.1
作者: hyuan
Github仓库: https://github.com/hyuan42/Rime-words-counter
时间: 2026-06-26

脚本功能：
1、多进程共享的数据核心层。负责读取 RIME 输入法写入的 CSV 上屏记录，
2、将增量字数累加到本地 JSON 历史文件，支持多设备合并（CRDT 取 max）、
3、日期翻转检测、定时自动清空 CSV，并通过事件总线向 GUI/状态栏推送更新。
"""

from __future__ import annotations

import csv
import json
import os
import socket
import sys
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import portalocker

SCHEMA_VERSION = 3


# ============ 路径与配置 ============
APP_NAME = "RimeWordsCounter"


def is_frozen() -> bool:
    """打包后的可执行文件运行时返回 True（py2app / PyInstaller 都设置 sys.frozen）。"""
    return getattr(sys, "frozen", False)


def app_support_dir() -> Path:
    """跨平台返回应用专属配置目录。"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def rime_user_dir() -> Path:
    """返回 RIME (鼠须管/小狼毫) 默认用户目录。"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Rime"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "Rime"
    return Path.home() / ".config" / "ibus" / "rime"


def default_csv_path() -> Path:
    return rime_user_dir() / "py_wordscounter" / "words_input.csv"


def default_json_path() -> Path:
    return app_support_dir() / "words_count_history.json"


def config_path() -> Path:
    return app_support_dir() / "config.json"


def log_path() -> Path:
    return app_support_dir() / "app.log"


# ============ 日志：把所有 print 输出统一写到 app.log（带轮转）============
class _TeeWriter:
    """同时写到原 stdout 和日志文件；每行带时间戳和进程标签。"""
    def __init__(self, original, file_obj, tag: str):
        self._original = original
        self._file = file_obj
        self._tag = tag
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, data):
        try:
            self._original.write(data)
            self._original.flush()
        except Exception:
            pass
        with self._lock:
            self._buf += data
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                try:
                    self._file.write(f"[{stamp}] [{self._tag}] {line}\n")
                    self._file.flush()
                except Exception:
                    pass

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass
        try:
            self._file.flush()
        except Exception:
            pass


def _rotate_log_if_needed(path: Path, max_bytes: int = 1_000_000, keep: int = 3):
    """超过 max_bytes 就把当前文件转储成 .1，.1 转 .2，依此类推；超 keep 个删除。"""
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return
        # 从最老的开始往后挪
        for i in range(keep, 0, -1):
            old = path.with_suffix(path.suffix + f".{i}")
            new = path.with_suffix(path.suffix + f".{i + 1}")
            if old.exists():
                if i == keep:
                    old.unlink()  # 最老的删掉
                else:
                    old.rename(new)
        # 当前文件 → .1
        path.rename(path.with_suffix(path.suffix + ".1"))
    except OSError as e:
        print(f"[log] 轮转失败: {e}", file=sys.__stderr__)


_logger_setup = False


def setup_logger(tag: str):
    """把当前进程的 stdout/stderr 重定向到 app.log（双写到原 std），并在头部加分隔符。

    只在 config.json 的 enable_log=true 时才启用。
    tag: 进程标签（"statusbar" / "gui" / "settings"）
    """
    global _logger_setup
    if _logger_setup:
        return
    _logger_setup = True

    # 检查配置开关
    try:
        cfg = load_config()
        if not cfg.enable_log:
            return  # 日志功能未启用，stdout/stderr 保持原状
    except Exception:
        return  # 配置读取失败，安全起见不启用日志

    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_log_if_needed(path)
        f = open(path, "a", encoding="utf-8", buffering=1)  # line buffered
        # 启动分隔符（方便用户在长日志里定位）
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"\n========== [{stamp}] [{tag}] 进程启动 PID={os.getpid()} ==========\n")
        f.flush()

        sys.stdout = _TeeWriter(sys.__stdout__, f, tag)
        sys.stderr = _TeeWriter(sys.__stderr__, f, tag)
    except Exception as e:
        print(f"[log] setup_logger 失败: {e}", file=sys.__stderr__)


_DEFAULT_CONFIG_TEMPLATE = """{
  "_comment": "字数统计工具配置文件。修改后即时生效。可以直接在【设置】窗口中修改对应的参数，或者在该文件中手动修改",
  "csv_path": "",
  "_csv_path_help": "RIME 上屏 CSV 路径。留空 = 自动用 RIME 用户目录下的 py_wordscounter/words_input.csv，要和 words_counter.lua 里的 CUSTOM_CSV_PATH 保持一致。",
  "json_path": "",
  "_json_path_help": "字数统计 JSON 历史文件。留空 = 放到本应用配置目录。多设备同步建议改到 iCloud/OneDrive/Dropbox 等云盘路径。",
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
  "_enable_plaintext_help": "是否开启明文版采集,开启后 CSV 第三列记录上屏原文。"
}
"""


class Config:
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
        host = socket.gethostname().split(".")[0] or "default"
        return host

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


def load_config() -> Config:
    """读取配置文件；不存在则用默认模板创建一份。"""
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
    cfg = Config(raw)
    # 提前把目标目录建好，避免后续写文件时报错；失败不致命（用默认路径兜底）
    for p in (cfg.csv_path, cfg.json_path):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"[config] 无法创建目录 {p.parent}: {e}")
    return cfg


def save_config(updates: dict) -> None:
    """把 updates 合并到 config.json。空字符串字段表示"用默认值"。

    保留所有以 `_` 开头的字段（注释），只更新业务字段。
    """
    cfg_path = config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    # 读现有内容（保留注释字段和未涉及的字段）
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
    """返回 app 包内或源码目录下的 words_counter.lua，找不到返回 None。"""
    # py2app 打包后设置 RESOURCEPATH 指向 Contents/Resources
    resource_path = os.environ.get("RESOURCEPATH")
    if resource_path:
        p = Path(resource_path) / "words_counter.lua"
        if p.exists():
            return p
    # 源码运行：__file__ 同目录
    p = Path(__file__).parent / "words_counter.lua"
    if p.exists():
        return p
    return None


def sync_lua_plaintext(enable: bool) -> tuple[bool, str]:
    """将 RIME 用户目录的 words_counter.lua 中 ENABLE_PLAINTEXT 同步为 enable。

    若安装的是旧版（无 ENABLE_PLAINTEXT 行），自动用打包内的新版覆盖后再写入。
    返回 (成功, 信息)。
    """
    import re
    path = lua_path()
    new_val = "true" if enable else "false"

    def _patch(text: str) -> tuple[str, int]:
        return re.subn(
            r"^(local ENABLE_PLAINTEXT\s*=\s*)(true|false)(\s*--.*)?$",
            lambda m: f"{m.group(1)}{new_val}  -- true = 第三列记录上屏原文",
            text,
            count=1,
            flags=re.MULTILINE,
        )

    try:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            new_text, count = _patch(text)
            if count == 0:
                # 旧版 lua，没有 ENABLE_PLAINTEXT 行 → 用新版覆盖
                bundled = _bundled_lua_path()
                if bundled is None:
                    return False, "找不到内置新版 Lua 文件，无法自动升级"
                text = bundled.read_text(encoding="utf-8")
                new_text, count = _patch(text)
                if count == 0:
                    return False, "内置 Lua 文件中也未找到 ENABLE_PLAINTEXT，请联系开发者"
        else:
            # 用户还未安装，从 bundle 直接部署
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


# ============ 事件回调总线 ============
class _CallbackBus:
    """简单的线程安全回调注册表，让 GUI / 状态栏等订阅"数据更新"事件。"""

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
                print(f"[wc_core] 回调执行失败: {e}")


bus = _CallbackBus()


# ============ JSON schema v3 helpers ============
# 存储结构：
#   顶层 daily/monthly/yearly/total = 历史聚合数据（昨天及以前）
#   devices[x].today_count = 今天这台设备累计的字数（跨设备加总显示）
#   每天 00:00 自动把 today_count 汇合进历史，然后清零

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
    from datetime import date as _date
    if raw.get("schema") == SCHEMA_VERSION:
        raw.setdefault("devices", {}).setdefault(device_id, _empty_device_node())
        return raw

    state: dict = {
        "schema": SCHEMA_VERSION,
        "today": _date.today().isoformat(),
        "daily": {}, "monthly": {}, "yearly": {}, "total": 0,
        "devices": {},
    }

    if raw.get("schema") == 2:
        # v2: 各设备各有 daily/monthly/yearly/total，全部合并进历史
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
    from datetime import date as _date
    today_str = _date.today().isoformat()
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
    """打开（或创建）JSON 文件，返回升级到 v3 的 state。

    特殊处理：如果 json_path 不存在，但 config.json 指向了别的路径（热重载场景），
    则不在旧路径创建文件，返回空状态让 processor 优雅退化。
    """
    from datetime import date as _date

    if not os.path.exists(json_path) or os.path.getsize(json_path) == 0:
        # 检查 config.json 是否指向了别的路径（设置刚改过、热重载中）
        try:
            cfg = load_config()
            config_json_path = str(cfg.json_path)
            if config_json_path != json_path and os.path.exists(config_json_path):
                # config 已指向新路径且新路径存在 → 旧 processor 应该退役，不要在旧路径创建
                print(f"[wc_core] JSON 路径已变更到 {config_json_path}，旧路径 {json_path} 不再创建")
                return {
                    "schema": SCHEMA_VERSION,
                    "today": _date.today().isoformat(),
                    "daily": {}, "monthly": {}, "yearly": {}, "total": 0,
                    "devices": {device_id: _empty_device_node()},
                }
        except Exception:
            pass  # config 读取失败，继续正常创建

        # 正常首次运行或 config 未变 → 创建 JSON
        os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
        state = {
            "schema": SCHEMA_VERSION,
            "today": _date.today().isoformat(),
            "daily": {}, "monthly": {}, "yearly": {}, "total": 0,
            "devices": {device_id: _empty_device_node()},
        }
        with safe_file(json_path, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        return state

    with safe_file(json_path, "r") as f:
        raw = json.load(f)
    return _migrate(raw, device_id)


def _csv_signature(csv_path: str) -> str:
    """返回 CSV "身份" 字符串，用于检测清空/重建。"""
    try:
        st = os.stat(csv_path)
        return f"{getattr(st, 'st_ino', 0)}-{int(st.st_ctime)}-{st.st_size}"
    except OSError:
        return ""


# ============ 数据处理 ============
class DataProcessor:
    """读取 CSV、累加到 JSON 的核心逻辑。线程安全。"""

    _instance_lock = threading.Lock()
    _process_lock = threading.Lock()  # 序列化 process_data，避免重入

    def __init__(self, csv_path: str, json_path: str, device_id: str):
        self.csv_path = csv_path
        self.json_path = json_path
        self.device_id = device_id

    # -------- 数据读取 --------
    def read_state(self) -> dict:
        """读取完整 state（不做累加），调用方通常关心 aggregate(state)。"""
        return _ensure_json(self.json_path, self.device_id)

    def aggregate(self) -> dict:
        return _aggregate(self.read_state())

    # -------- 增量处理 --------
    def process_data(self) -> dict:
        """读取 CSV 增量，检测日期翻转，写回 JSON，返回最新聚合视图。"""
        from datetime import date as _date
        with self._process_lock:
            state = _ensure_json(self.json_path, self.device_id)

            # 日期翻转检测：若存储的 today != 实际今天，先 flush 昨天的计数
            stored_today = state.get("today", "")
            actual_today = _date.today().isoformat()
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
        """从 node['csv_offset'] 开始读，返回 (timestamp, count) 列表，并更新 offset。"""
        if not os.path.exists(self.csv_path):
            return []

        entries: list[tuple[datetime, int]] = []
        # 用二进制定位 + 文本解析，规避换行符差异
        with safe_file(self.csv_path, "r") as f:
            offset = int(node.get("csv_offset", 0))
            # 如果还没读过文件，跳过 header 行
            if offset == 0:
                header = f.readline()
                if not header:
                    node["csv_offset"] = f.tell()
                    return []
                if header.strip().split(",")[:2] != ["timestamp", "chinese_count"]:
                    # header 不对，按 0 重置（兼容老格式 "timestamp","chinese_count"）
                    if header.strip().replace('"', "").split(",")[:2] != ["timestamp", "chinese_count"]:
                        return []
                offset = f.tell()
            else:
                f.seek(offset)

            for line in f:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                # 兼容两种格式：带引号的旧版 和 不带引号的新版
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

    # -------- 清空 CSV --------
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
                # today_count 保留：CSV 清空不等于撤销今天已统计的字数
                f.seek(0)
                json.dump(state, f, indent=2, ensure_ascii=False)
                f.truncate()
            bus.emit(_aggregate(state))
            return True
        except Exception as e:
            print(f"[wc_core] 清空 CSV 失败: {e}")
            return False


def rename_device(json_path: str, old_id: str, new_id: str) -> None:
    """把历史 JSON 里的设备节点从 old_id 重命名为 new_id，避免改名后双重计数。

    若 new_id 已存在（曾用过该名），则保留 new_id 节点，删除 old_id 节点。
    """
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
        print(f"[wc_core] 设备节点重命名失败: {e}")


# ============ 定时器：不再依赖 schedule，避免 1 小时步长导致的延迟 ============
def _last_clear_state_file() -> Path:
    """持久化"上次清理时间"，保证 app 重启后还知道距离上次清理的天数。"""
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
        print(f"[wc_core] 写入清理时间戳失败: {e}")


class _DailyScheduler:
    """可取消的间隔定时任务：每隔 interval_days 天的 hour:minute 执行 callback。"""
    def __init__(self, callback: Callable[[], None], hour: int, minute: int, interval_days: int = 1):
        self._stop_event = threading.Event()
        self._callback = callback
        self._hour = hour
        self._minute = minute
        self._interval_days = max(1, interval_days)
        self._thread = threading.Thread(target=self._runner, daemon=True, name="wc_daily_scheduler")
        self._thread.start()

    def _next_target(self) -> datetime:
        """计算下次触发时间：以"上次执行时间"为基准，加 interval_days 天后的 HH:MM。
        若没有上次记录，按"今天 HH:MM"，已过则跳到 interval_days 天后。
        """
        from datetime import timedelta
        now = datetime.now()
        last = _read_last_clear_time()
        if last is not None:
            # 基于上次执行时间 + interval_days 天
            base_date = last.date() + timedelta(days=self._interval_days)
            target = datetime.combine(base_date, datetime.min.time()).replace(
                hour=self._hour, minute=self._minute)
        else:
            # 首次运行：今天 HH:MM 没过就用今天，过了就加 interval_days 天
            target = now.replace(hour=self._hour, minute=self._minute, second=0, microsecond=0)
            if target <= now:
                target = target + timedelta(days=self._interval_days)
        # 确保目标时间在未来（防止配置变化、时钟漂移导致目标在过去）
        while target <= now:
            target = target + timedelta(days=self._interval_days)
        return target

    def _runner(self):
        while not self._stop_event.is_set():
            target = self._next_target()
            wait_s = max(1.0, (target - datetime.now()).total_seconds())
            print(f"[scheduler] 下次清理: {target.strftime('%Y-%m-%d %H:%M:%S')} "
                  f"(间隔 {self._interval_days} 天)")
            # 用 wait 而不是 sleep，支持提前唤醒
            if self._stop_event.wait(timeout=wait_s):
                break  # 被 stop() 唤醒
            try:
                self._callback()
                _write_last_clear_time(datetime.now())
            except Exception as e:
                print(f"[wc_core] 定时任务执行失败: {e}")

    def stop(self):
        """停止定时器（提前唤醒线程）。"""
        self._stop_event.set()


def schedule_daily(callback: Callable[[], None], hour: int = 0, minute: int = 0,
                    interval_days: int = 1) -> _DailyScheduler:
    """每隔 interval_days 天的 hour:minute 执行 callback，返回可取消的 scheduler。

    interval_days=1 即每天，=7 即每周。
    """
    return _DailyScheduler(callback, hour, minute, interval_days)

