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
        'wh',
        'wh.core',
        'wh.core.db',
        'wh.core.dbconn',
        'wh.core.errors',
        'wh.core.export',
        'wh.core.maintain',
        'wh.core.paths',
        'wh.core.potpl',
        'wh.core.query',
        'wh.core.router',
        'wh.core.schema',
        'wh.core.sysinfo',
        'wh.core.tpl',
        'wh.core.util',
        'wh.count',
        'wh.desktop',
        'wh.desktop.qt',
        'wh.desktop.shell',
        'wh.dispatch',
        'wh.importer',
        'wh.importer.base',
        'wh.importer.txn',
        'wh.importer.wide',
        'wh.inv',
        'wh.po',
        'wh.po.amount',
        'wh.po.head',
        'wh.po.query',
        'wh.po.receive',
        'wh.po.status',
        'wh.po.summary',
        'wh.po.web',
        'wh.po.web.common',
        'wh.po.web.detail',
        'wh.po.web.export',
        'wh.po.web.home',
        'wh.po.web.new',
        'wh.po.web.order',
        'wh.po.web.settle',
        'wh.po.web.supplier',
        'wh.sheet',
        'wh.sheet.batch',
        'wh.sheet.bridge',
        'wh.sheet.cols',
        'wh.sheet.colsapi',
        'wh.sheet.engine',
        'wh.sheet.engine.book',
        'wh.sheet.engine.cell',
        'wh.sheet.engine.core',
        'wh.sheet.engine.ctx',
        'wh.sheet.engine.eval',
        'wh.sheet.engine.ironcalc',
        'wh.sheet.engine.lazy',
        'wh.sheet.engine.lazy_agg',
        'wh.sheet.engine.lazy_cond',
        'wh.sheet.engine.lazy_logic',
        'wh.sheet.engine.lazy_ref',
        'wh.sheet.engine.sheet',
        'wh.sheet.io',
        'wh.sheet.io.csv',
        'wh.sheet.io.xlsx',
        'wh.sheet.kernel',
        'wh.sheet.kernel.addr',
        'wh.sheet.kernel.convert',
        'wh.sheet.kernel.dt',
        'wh.sheet.kernel.finance',
        'wh.sheet.kernel.funcs',
        'wh.sheet.kernel.info',
        'wh.sheet.kernel.lexer',
        'wh.sheet.kernel.logic',
        'wh.sheet.kernel.lookup',
        'wh.sheet.kernel.num',
        'wh.sheet.kernel.parser',
        'wh.sheet.kernel.registry',
        'wh.sheet.kernel.stat',
        'wh.sheet.kernel.style',
        'wh.sheet.kernel.text',
        'wh.sheet.ops',
        'wh.sheet.ops.analyze',
        'wh.sheet.ops.clip',
        'wh.sheet.ops.extra',
        'wh.sheet.ops.fill',
        'wh.sheet.ops.fmt',
        'wh.sheet.ops.group',
        'wh.sheet.ops.undo',
        'wh.sheet.ops.valid',
        'wh.sheet.web',
        'wh.sheet.web.api',
        'wh.sheet.web.book',
        'wh.sheet.web.common',
        'wh.sheet.web.edit',
        'wh.sheet.web.fmt',
        'wh.sheet.web.io_rt',
        'wh.sheet.web.pages',
        'wh.sheet.web.univer',
        'wh.sheet.web.view',
        'wh.stat',
        'wh.stat.center',
        'wh.stat.report',
        'wh.table',
        'wh.table.form',
        'wh.table.formula',
        'wh.table.helpers',
        'wh.table.model',
        'wh.table.ops',
        'wh.table.parse',
        'wh.table.render',
        'wh.txn',
        'wh.txn.browse',
        'wh.txn.form',
        'wh.txn.importer',
        'wh.ui',
        'wh.ui.nav',
        'wh.ui.theme',
        'wh.ui.widgets'
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
