"""Qt WebEngine 独立窗口 —— 自带 Chromium，不依赖系统 WebView2。

为什么要有这个模块
--------------------
pywebview 在 Windows 上默认走 edgechromium，依赖两样外部东西：
  1. 用户机器装了 Edge WebView2 运行时
  2. pythonnet（需要 .NET 4.0+，Python 3.13 上有已知构建失败）

任一缺失就悄悄退化成浏览器 —— 用户只觉得"这怎么还是个网页"，
却不知道发生了什么。Qt WebEngine 把 Chromium 一起打进程序，
不依赖系统组件，行为可预测，这是选它的唯一理由。

职责：只负责"开一个窗口把网页装进去"。
服务启动、单实例、图标、日志都复用 wh.desktop 里已有的东西。
"""
import os
import sys
import time
import socket
import threading
import traceback

try:
    from . import desktop
except ImportError:                     # 允许被单独 import（调试用）
    desktop = None

log = (desktop.log if desktop else (lambda *a, **k: None))


# ---------------------------------------------------------------- Qt 探测
#: 探测结果缓存，避免每次重复导入
_QT = None


def _load_qt():
    """按 PySide6 → PyQt6 → PyQt5 的顺序找一个能用的 Qt 绑定。

    返回一个 dict：{'mod': 名字, 'app': QApplication类, 'view': QWebEngineView类, ...}
    全都装不上就返回 None（调用方会退回 pywebview / 浏览器）。

    PySide6 排第一：PyInstaller 官方支持、LGPL、打包行为最可预测。
    """
    global _QT
    if _QT is not None:
        return _QT if _QT else None

    for name in ('PySide6', 'PyQt6', 'PyQt5'):
        try:
            if name == 'PySide6':
                from PySide6.QtWidgets import QApplication, QMainWindow
                from PySide6.QtWebEngineWidgets import QWebEngineView
                from PySide6.QtWebEngineCore import (
                    QWebEngineSettings, QWebEngineProfile, QWebEnginePage)
                from PySide6.QtCore import Qt, QUrl, QTimer
                from PySide6.QtGui import QIcon
            elif name == 'PyQt6':
                from PyQt6.QtWidgets import QApplication, QMainWindow
                from PyQt6.QtWebEngineWidgets import QWebEngineView
                from PyQt6.QtWebEngineCore import (
                    QWebEngineSettings, QWebEngineProfile, QWebEnginePage)
                from PyQt6.QtCore import Qt, QUrl, QTimer
                from PyQt6.QtGui import QIcon
            else:
                from PyQt5.QtWidgets import QApplication, QMainWindow
                from PyQt5.QtWebEngineWidgets import QWebEngineView
                from PyQt5.QtWebEngineCore import (
                    QWebEngineSettings, QWebEngineProfile, QWebEnginePage)
                from PyQt5.QtCore import Qt, QUrl, QTimer
                from PyQt5.QtGui import QIcon
            _QT = dict(mod=name, app=QApplication, win=QMainWindow,
                       view=QWebEngineView, settings=QWebEngineSettings,
                       profile=QWebEngineProfile, page=QWebEnginePage,
                       qt=Qt, url=QUrl, timer=QTimer, icon=QIcon)
            log('Qt 后端：%s' % name)
            return _QT
        except Exception as e:
            log('%s 不可用：%s' % (name, e))
            continue

    _QT = False          # 探测过但全失败，别再试了
    return None


def has_qt():
    """装了 Qt 绑定吗（给 run.py 判断用）"""
    return _load_qt() is not None


# ---------------------------------------------------------------- 环境准备
def _prepare_env():
    """WebEngine 在受限环境下（root、容器、无 GPU）需要几个开关。

    不设的话常见症状是窗口一片漆黑或直接闪退。
    只在缺失时补，不覆盖用户自己设的值。
    """
    # root 下 Chromium 的沙箱起不来（容器里最常见）
    if os.name != 'nt' and os.geteuid() == 0:
        flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
        if '--no-sandbox' not in flags:
            os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (
                flags + ' --no-sandbox --disable-setuid-sandbox').strip()
    # 无显示器（CI、服务器）时允许离屏启动，避免直接崩
    if os.name != 'nt' and not os.environ.get('DISPLAY'):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    # 禁用 GPU（软件渲染更稳，画质无差别）
    flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
    if '--disable-gpu' not in flags:
        os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (
            flags + ' --disable-gpu').strip()


def _wait_up(port, timeout=15.0):
    """等服务真的能连上再开窗口，否则用户会先看到一秒白屏"""
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = socket.create_connection(('127.0.0.1', port), 0.5)
            s.close()
            return True
        except OSError:
            time.sleep(0.15)
    return False


# ---------------------------------------------------------------- 主流程
def run_qt_window(app, port, title='仓库管理系统', size=(1280, 860)):
    """用 Qt WebEngine 开独立窗口。返回 True 表示窗口正常跑完一轮。

    app   : Flask 实例（在后台线程里跑）
    port  : 服务端口
    失败会返回 False，由调用方退回 pywebview / 浏览器。
    """
    q = _load_qt()
    if not q:
        return False

    try:
        _prepare_env()
    except Exception as e:
        log('环境准备失败（继续）：%s' % e)

    url = 'http://127.0.0.1:%d' % port
    threading.Thread(
        target=lambda: app.run('127.0.0.1', port, debug=False, threaded=True,
                               use_reloader=False),
        daemon=True).start()

    if not _wait_up(port):
        log('服务 %d 秒内没起来，放弃 Qt 窗口' % 15)
        return False

    argv = list(sys.argv)
    if os.name != 'nt' and os.environ.get('QT_QPA_PLATFORM') == 'offscreen':
        argv = [argv[0], '-platform', 'offscreen']

    try:
        qt_app = q['app'](argv)
    except Exception as e:
        log('QApplication 创建失败：%s' % e)
        log(traceback.format_exc())
        return False

    icon_path = desktop.app_icon() if desktop else None
    try:
        win = q['win']()
        win.setWindowTitle(title)
        if icon_path:
            win.setWindowIcon(q['icon'](icon_path))
        win.resize(size[0], size[1])
        try:
            win.setMinimumSize(900, 600)
        except Exception:
            pass

        view = q['view'](win)
        win.setCentralWidget(view)

        # 让网页能用 localStorage 等存储（表格模块要用）
        try:
            st = view.settings()
            for attr in ('LocalStorageEnabled', 'LocalContentCanAccessRemoteUrls',
                         'JavascriptEnabled', 'PluginsEnabled'):
                try:
                    st.setAttribute(getattr(q['settings'], attr), True)
                except Exception:
                    pass
        except Exception as e:
            log('WebEngine 设置失败（继续）：%s' % e)

        view.setUrl(q['url'](url))
        win.show()
        log('Qt 窗口已显示：%s（%s）' % (url, q['mod']))
        qt_app.exec()
        log('Qt 窗口正常退出')
        return True
    except Exception as e:
        log('Qt 窗口异常：%s' % e)
        log(traceback.format_exc())
        # 已经进了事件循环又被打断，也要把进程收干净
        try:
            qt_app.exit(1)
        except Exception:
            pass
        return False
