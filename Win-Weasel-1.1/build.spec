# PyInstaller 打包配置 (Windows)
#
# 用法（在 Windows 上执行）：
#     pip install pyinstaller pystray pillow pywin32 portalocker watchdog
#     cd Win-Weasel-Number-1.0\
#     pyinstaller build.spec
#
# 产物：
#     dist\字数统计.exe   ← 单文件可执行，双击运行
#     build\               ← 中间产物，可删
#
# 注意：
# 1. 必须在 Windows 上执行，不能在 Mac/Linux 上交叉打包。
# 2. --windowed 模式下双击不弹黑色控制台；调试时可以临时改成 console=True。
# 3. 首次启动用户的配置文件会自动生成在
#    %APPDATA%\RimeWordsCounter\config.json
# 4. 未做代码签名，首次运行可能触发 SmartScreen，点"更多信息 → 仍要运行"即可。
# 5. exe 体积约 30-40 MB（带完整 Python + Tk + PIL + pystray + watchdog）。

# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ["words_counter.py"],
    pathex=[],
    binaries=[],
    datas=[
        # 把 Lua 脚本一起带上，方便用户在 exe 同级目录看到它
        ("words_counter.lua", "."),
    ],
    hiddenimports=[
        "pystray._win32",
        "PIL._tkinter_finder",
        "watchdog.observers.read_directory_changes",
        "watchdog.observers.polling",
        "win32timezone",  # pywin32 内部依赖
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "numpy", "pandas", "scipy",  # 不需要
        "PyQt5", "PyQt6", "PySide2", "PySide6",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="字数统计",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.ico",
)
