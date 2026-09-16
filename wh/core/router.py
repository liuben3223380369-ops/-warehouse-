# -*- coding: utf-8 -*-
"""路由收集器 —— 让各模块"先声明、后装配"

为什么不用 Flask 的 Blueprint：
    Blueprint 会给 endpoint 加上前缀（'stock' 变成 'inv.stock'），
    模板里已经写好的 url_for('stock') 会全部失效。

Router 的做法：模块里照常写 @bp.route(...)，但只是**登记**下来，
最后由调度文件统一挂到 app 上，endpoint 保持函数原名不变。

这样每个模块可以独立开发和测试，装配权留给调度文件。
"""


class Router(object):
    """登记 (rule, view_func, options)，等 register 时再绑定"""

    def __init__(self, name=''):
        self.name = name
        self.routes = []

    def route(self, rule, **options):
        def deco(func):
            self.routes.append((rule, func, options))
            return func
        return deco

    # 兼容 Blueprint 的常见用法
    def before_request(self, *a, **kw):
        def deco(f):
            self.routes.append((None, f, {'_hook': 'before_request'}))
            return f
        return deco

    def register(self, app):
        """把登记的路由挂到 app 上"""
        n = 0
        for rule, func, options in self.routes:
            opts = dict(options or {})
            if opts.pop('_hook', None):
                continue
            endpoint = opts.pop('endpoint', None) or func.__name__
            methods = opts.pop('methods', None)
            try:
                app.add_url_rule(rule, endpoint=endpoint, view_func=func,
                                 methods=methods, **opts)
                n += 1
            except Exception as e:
                # 路由冲突不该让整个程序起不来，但要留下线索
                try:
                    import traceback
                    with open('warehouse.log', 'a', encoding='utf-8') as f:
                        f.write('[router] %s 注册失败 %s: %s\n'
                                % (self.name, rule, traceback.format_exc()))
                except Exception:
                    pass
        return n

    def __repr__(self):
        return '<Router %s routes=%d>' % (self.name, len(self.routes))
