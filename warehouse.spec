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
    ('seed_materials.json', '.'),
]

# Flask/Jinja2 的模块是动态导入的，静态分析抓不全，这里手动挂上
hiddenimports = [
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
]

a = Analysis(
    ['app.py'],
    pathex=[HERE],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'pandas', 'PIL',
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='仓库管理系统',   # 无 COLLECT = 单文件模式，就一个 exe
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
    icon=None,             # 想换图标就填 icon='app.ico'
)
