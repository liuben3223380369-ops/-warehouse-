# -*- coding: utf-8 -*-
"""仓库管理系统 —— 启动入口

用法：
    python run.py            # 自动找端口，浏览器打开
    python run.py 9000       # 指定端口
    python run.py --window   # 强制独立窗口（需要 pywebview）
    python run.py --browser  # 强制浏览器

真正的装配在 wh/dispatch.py，这里只负责：起服务、开窗口/浏览器、兜住崩溃。
"""
import os
import sys
import time

# Windows 中文控制台是 GBK，print 一个 emoji 就会崩。先切容错模式。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from wh import __version__ as __ver
from wh import desktop                                      # noqa: E402
from wh.core import db                                      # noqa: E402
from wh.core.util import say, cleanup_tmp                   # noqa: E402


def open_browser_later(port, delay=1.2):
    """浏览器模式下自动打开浏览器"""
    import threading
    import webbrowser

    def go():
        time.sleep(delay)
        try:
            webbrowser.open('http://127.0.0.1:%d' % port)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


def main():
    from wh.dispatch import create_app

    args = [a for a in sys.argv[1:]]
    force_browser = '--browser' in args
    force_window = '--window' in args
    args = [a for a in args if not a.startswith('--')]

    try:
        port = int(args[0]) if args else desktop.free_port()
    except ValueError:
        port = desktop.free_port()

    cleanup_tmp()

    # 装配所有模块（数据库初始化、补指纹都在这里做）
    app = create_app()

    try:
        st = db.stats()
    except Exception as e:          # 数据库损坏等致命错误
        msg = ['数据库打不开：%s' % e]
        try:
            msg += db.check_integrity()
        except Exception:
            pass
        msg.append('也可以把 %s 改名（比如加 .old），程序会自动新建一个空库，'
                   '再用备份还原。' % os.path.basename(db.DB_PATH))
        desktop.log('启动失败：\n  ' + '\n  '.join(msg))
        for m in ['', '  !! 启动失败 !!'] + ['  ' + x for x in msg] + ['']:
            say(m)
        say('  详细信息已写入 %s' % desktop.LOG)
        if getattr(sys, 'frozen', False):
            time.sleep(30)
        return

    banner = [
        '-' * 46,
        '  仓库管理系统 v' + __ver,
        '  数据: %s' % st['path'],
        '  物料 %d 种 · 单据 %d 条 · 预警 %d 项'
        % (st['materials'], st['txns'], st['alerts']),
    ]
    iss = []
    try:
        iss = db.check_integrity()
    except Exception:
        pass
    if iss:
        banner.append('  [!] 数据体检发现 %d 个问题，访问 /sys 查看' % len(iss))

    frozen = getattr(sys, 'frozen', False)
    # 打包成 exe 默认开独立窗口；源码运行默认浏览器（方便调试）
    want_window = force_window or (frozen and not force_browser)

    if want_window and desktop.has_webview():
        banner.append('  窗口模式: 已启动独立窗口')
        banner.append('  日志: %s' % desktop.LOG)
        banner.append('-' * 46)
        for b in banner:
            say(b)
            desktop.log(b.strip())
        ok = desktop.run_window(app, port)
        if not ok:                       # 窗口起不来就退回浏览器
            say('  独立窗口启动失败，已退回浏览器模式')
            open_browser_later(port)
            app.run('127.0.0.1', port, debug=False, threaded=True)
    else:
        if want_window and not force_browser:
            banner.append('  提示: 缺少 pywebview，已用浏览器模式')
        banner.append('  访问: http://127.0.0.1:%d' % port)
        banner.append('  停止: 关掉这个窗口 或 Ctrl+C')
        banner.append('-' * 46)
        for b in banner:
            say(b)
        if frozen:
            open_browser_later(port)
        try:
            app.run('127.0.0.1', port, debug=False, threaded=True)
        except OSError as e:
            say('  启动失败：%s' % e)
            say('  端口 %d 可能被占用，换个端口：仓库管理系统.exe 9000' % port)
            if frozen:
                time.sleep(8)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # 窗口模式看不见控制台，出错必须落到文件里
        import traceback
        try:
            desktop.log('崩溃：\n' + traceback.format_exc())
        except Exception:
            pass
        try:
            traceback.print_exc()
        except Exception:
            say('  （错误详情无法打印，已写入 warehouse.log）')
        time.sleep(10)
    finally:
        try:
            db.close()
        except Exception:
            pass
        if getattr(sys, 'frozen', False):
            time.sleep(1.5)
