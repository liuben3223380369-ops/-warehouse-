# -*- coding: utf-8 -*-
"""Android 启动入口（由 MainActivity 通过 ChaQuopy 调用）

只做三件事：
    1. 把数据目录指向 App 私有目录（可写），源码目录在 APK 里是只读的
    2. 把 templates / static 复制到私有目录（Flask 要能 stat / open 真实文件）
    3. 在后台线程起 Flask，等端口真的能连上再把端口号交回给 WebView

    start_server(files_dir) -> int 端口号
"""
import os
import shutil
import socket
import threading
import time

SRC = os.path.dirname(os.path.abspath(__file__))


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _mirror(name, dst_root):
    """把源码里的 templates / static 复制到可写目录，返回最终可用路径

    先复制到 .tmp 目录再改名：static 现在有 12MB（表格引擎），
    中途被杀掉会留下半个目录，页面就会报“引擎资源缺失”。
    改名是原子操作，要么完整要么不存在，下次启动会自动重来。
    """
    src = os.path.join(SRC, name)
    dst = os.path.join(dst_root, name)
    tmp = dst + '.tmp'
    try:
        if os.path.isdir(dst):
            return dst
        if os.path.isdir(src):
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(src, tmp)
            os.rename(tmp, dst)
            return dst
    except Exception:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
    return src


def _wait_up(port, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            c = socket.create_connection(('127.0.0.1', port), 0.5)
            c.close()
            return True
        except Exception:
            time.sleep(0.25)
    return False


def start_server(files_dir):
    try:
        os.makedirs(files_dir, exist_ok=True)
    except Exception:
        pass

    # 1) 数据目录：数据库、备份、上传临时文件、日志都落在这里
    os.environ['WAREHOUSE_HOME'] = files_dir
    os.environ['WAREHOUSE_DB'] = os.path.join(files_dir, 'warehouse.db')

    # 2) 模板与静态资源：APK 里是只读虚拟路径，复制到私有目录后 Flask 才能正常 stat
    os.environ['WAREHOUSE_TEMPLATES'] = _mirror('templates', files_dir)
    os.environ['WAREHOUSE_STATIC'] = _mirror('static', files_dir)

    # 3) 起服务
    from wh.dispatch import create_app

    app = create_app()
    port = _free_port()

    def run():
        try:
            app.run('127.0.0.1', port, debug=False, threaded=True, use_reloader=False)
        except Exception as e:
            try:
                with open(os.path.join(files_dir, 'warehouse.log'), 'a',
                          encoding='utf-8') as f:
                    f.write('服务异常: %r\n' % (e,))
            except Exception:
                pass

    threading.Thread(target=run, daemon=True).start()
    _wait_up(port)
    return port
