"""桌面窗口模式 —— 让程序看起来就是一个正常安装的软件。

设计目标（打包成 exe 后的表现）：
  * 双击出**独立窗口**，没有地址栏、没有浏览器标签页
  * Alt+Tab 有自己的图标和标题
  * 双击第二次不会再开一个（单实例）
  * 窗口起不来时给出人话提示，而不是悄悄退化成浏览器

有 pywebview 就开窗口；所有后端都失败才退回浏览器（功能不变，只是样子不同）。
源码运行时默认浏览器，方便调试。
"""
import os
import sys
import time
import socket
import struct
import threading
import traceback

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'warehouse.log')

# 单实例锁文件（放数据目录，保证每次运行位置固定）
_LOCK_NAME = '.instance.lock'


def log(msg):
    """窗口模式没有黑窗口，出错全靠这个文件"""
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write('%s  %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except OSError:
        pass


def free_port(start=8080, tries=50):
    """自动找一个没被占用的端口，避免"端口被占用"直接起不来"""
    for p in range(start, start + tries):
        s = socket.socket()
        try:
            s.bind(('127.0.0.1', p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    return start


def app_icon():
    """程序图标的绝对路径。打包后从解包目录取，源码运行时从项目根取。
    找不到就返回 None（pywebview 会用默认图标，不影响启动）。"""
    try:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cands = [
            os.path.join(getattr(sys, '_MEIPASS', ''), 'app.ico'),
            os.path.join(here, 'app.ico'),
        ]
        for c in cands:
            if c and os.path.isfile(c):
                return c
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- 单实例
def _pid_alive(pid):
    """这个进程号还活着吗（跨平台，不引第三方依赖）"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == 'nt':
        try:
            import ctypes
            # PROCESS_QUERY_LIMITED_INFORMATION
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return False
    return True


def _lock_path():
    """锁文件放数据目录——和数据库同一层，位置固定且可写"""
    try:
        from wh.core import db
        return os.path.join(db.app_dir(), _LOCK_NAME)
    except Exception:
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            _LOCK_NAME)


def already_running():
    """已经有实例在跑了吗？顺带把自己的 pid 写进去。

    返回 True 表示"我不是第一个"，调用方应直接退出。
    只在打包成 exe 时启用——源码调试时多开很正常，不该拦。
    """
    if not getattr(sys, 'frozen', False):
        return False
    path = _lock_path()
    try:
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                old = f.read().strip()
            if old.isdigit() and _pid_alive(int(old)) and int(old) != os.getpid():
                return True
            # 死掉的旧锁，直接覆盖
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(str(os.getpid()))
    except Exception as e:
        log('单实例检查失败（不拦截）：%s' % e)
        return False
    # 正常退出时清掉锁
    import atexit

    def _cleanup():
        try:
            if os.path.isfile(path):
                with open(path, 'r', encoding='utf-8') as f:
                    if f.read().strip() == str(os.getpid()):
                        os.remove(path)
        except Exception:
            pass
    atexit.register(_cleanup)
    return False


def msgbox(text, title='仓库管理系统'):
    """弹一个系统提示框。窗口模式下没有控制台，只能这样告诉用户。
    Windows 用 MessageBoxW，其它平台尽力而为，失败就只写日志。"""
    log('[提示] %s | %s' % (title, text))
    if os.name == 'nt':
        try:
            import ctypes
            return ctypes.windll.user32.MessageBoxW(0, str(text), str(title), 0x40)
        except Exception:
            pass
    try:
        sys.stderr.write('\n%s\n%s\n' % (title, text))
        sys.stderr.flush()
    except Exception:
        pass


# ---------------------------------------------------------------- 后端探测
def has_webview():
    """能开独立窗口吗（Qt 或 pywebview 任一可用即可）"""
    if has_qt():
        return True
    try:
        import webview          # noqa: F401  仅探测是否可用
        return True
    except Exception as e:
        log('pywebview 不可用：%s' % e)
        return False


def has_qt():
    """装了 Qt 绑定吗。Qt WebEngine 自带 Chromium，比 pywebview 稳，优先用"""
    try:
        from . import qt
        return qt.has_qt()
    except Exception as e:
        log('Qt 不可用：%s' % e)
        return False


def _have_webview2():
    """Windows 上 Edge WebView2 运行时装了吗（看注册表）。
    没装的话 pywebview 的 edgechromium 后端必失败。"""
    if os.name != 'nt':
        return True
    try:
        import winreg
        keys = [
            r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients'
            r'\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
            r'SOFTWARE\Microsoft\EdgeUpdate\Clients'
            r'\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
        ]
        for k in keys:
            try:
                winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, k))
                return True
            except OSError:
                continue
    except Exception:
        pass
    return False


def _backends():
    """按可靠程度排出要尝试的后端顺序。
    None = 让 pywebview 自己挑；Windows 上它通常选 edgechromium。"""
    if os.name != 'nt':
        return [None, 'gtk', 'qt']
    order = []
    if _have_webview2():
        order.append('edgechromium')   # Win10/11 大多自带，画质最好
    order.append(None)                 # 自动挑
    order.append('mshtml')             # IE 内核，兜底，画面旧但几乎一定能起
    order.append('cef')                # 需要额外装 cefpython，一般没有
    return order


# ---------------------------------------------------------------- 主流程
def _wait_up(port, timeout=12.0):
    """等服务真的能连上再开窗口，否则会先白屏一下"""
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = socket.create_connection(('127.0.0.1', port), 0.5)
            s.close()
            return True
        except OSError:
            time.sleep(0.15)
    return False


def run_window(app, port, title='仓库管理系统', size=(1280, 860)):
    """开独立窗口。返回 True 表示成功。

    顺序：Qt WebEngine（自带 Chromium，最稳）→ pywebview 多后端 → 放弃。
    全失败返回 False，由调用方退回浏览器。
    """
    # ① Qt WebEngine：不依赖系统 WebView2 / .NET，优先
    try:
        from . import qt
        if qt.has_qt():
            if qt.run_qt_window(app, port, title=title, size=size):
                return True
            log('Qt 窗口没起来，转 pywebview')
    except Exception as e:
        log('Qt 窗口异常：%s' % e)

    # ② pywebview：轻便，但依赖系统组件
    try:
        import webview
    except Exception as e:
        log('pywebview 不可用：%s' % e)
        return False

    url = 'http://127.0.0.1:%d' % port
    threading.Thread(
        target=lambda: app.run('127.0.0.1', port, debug=False, threaded=True,
                               use_reloader=False),
        daemon=True).start()

    if not _wait_up(port):
        log('服务 %d 秒内没起来，放弃窗口模式' % 12)
        return False

    icon = app_icon()
    log('开窗口：%s  icon=%s' % (url, icon))
    errs = []
    for gui in _backends():
        try:
            webview.create_window(
                title, url,
                width=size[0], height=size[1],
                min_size=(900, 600),
                **( {'icon': icon} if icon else {}))
            webview.start(gui=gui, debug=False)
            log('窗口正常退出（后端=%s）' % gui)
            return True
        except Exception as e:
            errs.append('%s: %s' % (gui or 'auto', e))
            log('后端 %s 失败：%s' % (gui or 'auto', e))
            log(traceback.format_exc())
            continue

    log('所有窗口后端都失败：\n  ' + '\n  '.join(errs))
    if os.name == 'nt' and not _have_webview2():
        msgbox('程序已改用浏览器打开。\n\n'
               '想用独立窗口，请安装 Edge WebView2 运行时：\n'
               'https://developer.microsoft.com/microsoft-edge/webview2/')
    return False
