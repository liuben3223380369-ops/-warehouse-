# -*- mode: python ; coding: utf-8 -*-
"""仓库管理系统 打包配置（PyInstaller）

用法（在 Windows 本机、装好 Python 后）：
    pyinstaller warehouse.spec --noconfirm
产物在 dist\\仓库管理系统.exe

注意：PyInstaller 不支持交叉编译——想出 Windows 的 exe，
必须在 Windows 上跑这个命令，Linux/Mac 只能打出对应平台的程序。
"""
import os, sys

block_cipher = None
HERE = os.path.abspath('.')

# 随程序一起打进去的数据文件（源路径, 解包后的相对目录）
datas = [
    ('templates', 'templates'),
    # 表格引擎离线资源（static/univer，约 12MB）。
    # 不打进去的话 exe 里点「新引擎」全是 404，所以必须带上。
    ('static', 'static'),
    ('seed_materials.json', '.'),
    # 程序图标：既要嵌进 exe（下面 icon=），也要在运行时能取到
    # （pywebview 开窗时要读它，单文件模式下从 _MEIPASS 取）
    ('app.ico', '.'),
]

# Flask/Jinja2 的模块是动态导入的，静态分析抓不全，这里手动挂上
hiddenimports = [
    # 本项目自己的包：拆成模块后是普通 Python 包，
    # 但 PyInstaller 静态分析容易漏掉子模块，逐个挂上最稳
    'wh',
    'wh.dispatch',
    'wh.desktop',
    'wh.importer',
    'wh.core',
    'wh.core.db',
    'wh.core.util',
    'wh.core.errors',
    'wh.core.router',
    'wh.core.sysinfo',
    'wh.ui',
    'wh.ui.theme',
    'wh.ui.widgets',
    'wh.table',
    'wh.table.model',
    'wh.table.formula',
    'wh.table.parse',
    'wh.table.ops',
    'wh.table.export',
    'wh.table.render',
    'wh.table.routes',
    'wh.table.helpers',
    # 电子表格引擎（类 Excel 制表台）—— 词法/语法/函数/样式/地址各自一个文件
    'wh.table.sp_addr',
    'wh.table.sp_lexer',
    'wh.table.sp_parser',
    'wh.table.sp_funcs',
    'wh.table.sp_engine',
    'wh.table.sp_io',
    'wh.table.sp_style',
    'wh.table.sp_routes',
    'wh.table.sp_extra',
    'wh.inv',
    'wh.txn',
    'wh.po',
    'wh.po.logic',
    'wh.po.routes',
    'wh.stat',
    'wh.count',
    # 独立窗口（Qt WebEngine 自带 Chromium，优先于 pywebview）
    'wh.qtwin',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtNetwork',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebChannel',
    'PyQt6',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'PyQt6.QtWebEngineCore',
    'PyQt6.QtWebEngineWidgets',
    'flask',
    'jinja2',
    'jinja2.ext',
    'werkzeug',
    'werkzeug.serving',
    'werkzeug.wsgi',
    'markupsafe',
    'itsdangerous',
    'click',
    # openpyxl 的子模块是动态导入的，漏一个就会在 exe 里解析失败
    'openpyxl',
    'openpyxl.cell',
    'openpyxl.cell._writer',
    'openpyxl.cell.text',
    'openpyxl.chart',
    'openpyxl.comments',
    'openpyxl.drawing',
    'openpyxl.packaging',
    'openpyxl.styles',
    'openpyxl.utils',
    'openpyxl.worksheet',
    'openpyxl.worksheet._reader',
    'openpyxl.worksheet._writer',
    'openpyxl.reader',
    'openpyxl.reader.excel',
    'openpyxl.writer',
    'openpyxl.writer.excel',
    'xlrd',
    # 独立窗口用的（缺了会自动退回浏览器模式，不影响功能）
    'webview',
    'webview.platforms',
    'webview.platforms.winforms',
    'webview.platforms.edgechromium',
    'webview.platforms.cef',
    'pythonnet',
    'clr',
    'sqlite3',
    'webbrowser',
    'email.mime.multipart',
    'email.mime.text',
    'xlsxwriter',
]

a = Analysis(
    ['run.py'],
    pathex=[HERE],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'pandas', 'PIL',
        # 注意：不要排 PySide6 / PyQt6 —— 独立窗口要用
        'PyQt5', 'PySide2', 'IPython', 'pytest',
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
    [],                    # 二进制不塞进 exe，交给下面的 COLLECT
    exclude_binaries=True,
    name='仓库管理系统',   # 目录模式：exe + 依赖目录，WebEngine 更稳
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,         # 独立窗口模式：不弹黑窗，报错写进 warehouse.log
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico',        # exe 图标（任务栏 / Alt+Tab / 资源管理器都用它）
)

# 目录模式：WebEngine 的 QtWebEngineProcess / 资源 / 翻译都要在 exe 旁边，
# 单文件模式下它们被解压到临时目录，路径解析容易出错（典型症状：白屏）
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='仓库管理系统',
)
