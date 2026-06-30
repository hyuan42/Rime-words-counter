"""
macOS专用，把脚本打包成.app 安装包。

用法（任意路径下都可以跑）：
    pip install py2app
    python3 /path/to/Mac-Squirrel-Number-1.0/setup.py py2app

产物：
    Mac-Squirrel-Number-1.0/dist/字数统计.app   ←  双击即可运行
    Mac-Squirrel-Number-1.0/build/               ←  中间产物，可删

注意：
1. 必须在 macOS 上执行，不能在 Win/Linux 上交叉打包。
2. 第一次构建建议先用 `python setup.py py2app -A` 做 alias build（不复制 site-packages，
   启动快、便于调试）；正式分发再用不带 -A 的完整构建。
3. 用户首次启动 .app 时，配置文件会自动生成在
   ~/Library/Application Support/RimeWordsCounter/config.json
4. 未做代码签名，用户首次双击会被 Gatekeeper 拦，需要在"系统设置 → 隐私与安全性"
   里点"仍要打开"。如要避免，需要 Apple 开发者账号给 .app 签名 + 公证。
"""


import os
import sys

from setuptools import setup

# 切到 setup.py 所在目录：py2app 用 cwd 解析入口文件、data_files 等相对路径，
# 这样无论从哪里调用 setup.py 都能正确工作
_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)

APP_NAME = "字数统计"
APP = ["status_bar_app.py"]   # 入口：状态栏作为 .app 主程序

DATA_FILES = [
    # 把 Lua 脚本一起带上，方便用户从 .app 内部找到它
    "words_counter.lua",
]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": "icon.icns",        # .app 图标
    # LSUIElement=1 让 .app 不在 Dock 显示图标，只在状态栏出现，跟 rumps 配套
    "plist": {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "com.hyuan.rime-words-counter",
        "CFBundleVersion": "1.1.0",
        "CFBundleShortVersionString": "1.1.0",
        "LSUIElement": True,
        "NSHumanReadableCopyright": "© hyuan",
    },
    # 必须把 wc_core 和 words_counter 也算进来，否则状态栏延迟 import 会找不到
    "includes": [
        "wc_core",
        "words_counter",
        "rumps",
        "portalocker",
        "watchdog",
        "watchdog.observers",
        "watchdog.events",
    ],
    "packages": ["watchdog"],
    "strip": False,
    "optimize": 1,
}

setup(
    name=APP_NAME,
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
