"""桌面窗口模式（可选）。

有 pywebview 就弹出一个独立窗口，没有就自动退回浏览器模式——
功能完全一样，只是窗口样子不同。打包成 exe 后默认走窗口模式。
"""
import os, time, threading, socket, traceback

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'warehouse.log')

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

def has_webview():
    try:
        import webview          # noqa: F401  仅探测是否可用
        return True
    except Exception:
        return False

def run_window(app, port, title='仓库管理系统', size=(1200, 800)):
    """用 pywebview 开独立窗口。返回 True 表示成功了"""
    try:
        import webview
    except Exception as e:
        log('pywebview 不可用：%s' % e)
        return False

    url = 'http://127.0.0.1:%d' % port
    t = threading.Thread(
        target=lambda: app.run('127.0.0.1', port, debug=False, threaded=True,
                               use_reloader=False),
        daemon=True)
    t.start()

    # 等服务起来再开窗，否则会白屏一下
    for _ in range(60):
        try:
            s = socket.create_connection(('127.0.0.1', port), 0.5)
            s.close()
            break
        except OSError:
            time.sleep(0.15)

    try:
        webview.create_window(title, url, width=size[0], height=size[1],
                              min_size=(900, 600))
        webview.start()
        return True
    except Exception as e:
        log('开窗口失败：%s' % e)
        log(traceback.format_exc())
        return False
