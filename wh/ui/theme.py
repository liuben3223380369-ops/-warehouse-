# -*- coding: utf-8 -*-
"""UI 模块 · 主题

原则：简练直观。
  * 一套 CSS 变量控制全局配色，改一处全站生效
  * 只保留必要的几种语义色：正常 / 提示 / 警告 / 危险 / 成功
  * 不堆装饰：圆角、阴影、渐变能省就省，信息密度优先
  * 移动端优先，触摸目标不小于 40px
"""

# 语义色（改这里就能换肤）
COLORS = {
    'bg': '#f5f6f7',
    'card': '#ffffff',
    'line': '#e3e5e8',
    'text': '#1f2328',
    'muted': '#8b9096',
    'brand': '#1a6fd4',
    'ok': '#1a7f37',
    'warn': '#bf8700',
    'danger': '#c9262c',
    'soft': '#eef2f7',
}

NAV = [
    ('index', '🏠', '首页'),
    ('in', '📥', '入库'),
    ('out', '📤', '出库'),
    ('stock', '📊', '库存'),
    ('purchase', '🧾', '采购'),
    ('materials', '📦', '物料'),
]

# 更多页面（折叠在导航"更多"里）
MORE = [
    ('txns', '📋', '流水'),
    ('report', '📅', '月报'),
    ('tpls', '🗂', '模板'),
    ('po_tpls', '🗃', '采购模板'),
    ('suppliers', '🏭', '供应商'),
    ('sysinfo', '🔍', '自检'),
]


def css():
    """生成全局样式（base.html 里 {{ ui_css|safe }}）"""
    c = COLORS
    return """
:root{
 --bg:%(bg)s; --card:%(card)s; --line:%(line)s; --text:%(text)s;
 --muted:%(muted)s; --brand:%(brand)s; --ok:%(ok)s; --warn:%(warn)s;
 --danger:%(danger)s; --soft:%(soft)s;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--text);
 font:15px/1.5 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
 padding-bottom:64px}
a{color:var(--brand);text-decoration:none}
.wrap{max-width:960px;margin:0 auto;padding:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
 padding:12px;margin-bottom:10px}
h1,h2,h3{margin:0 0 8px;font-size:17px}
.muted{color:var(--muted);font-size:13px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
table{width:100%%;border-collapse:collapse;background:var(--card);font-size:14px}
th,td{border-bottom:1px solid var(--line);padding:8px 6px;text-align:left;
 white-space:nowrap}
th{background:var(--soft);font-weight:600;position:sticky;top:0;z-index:2}
td.num,th.num{text-align:right}
input,select,textarea{font:inherit;padding:8px;border:1px solid var(--line);
 border-radius:6px;background:#fff;color:var(--text);min-height:38px}
input:focus,select:focus{outline:2px solid var(--brand);outline-offset:-1px}
button,.btn{display:inline-block;padding:9px 14px;min-height:40px;border-radius:6px;
 border:1px solid var(--line);background:#fff;color:var(--text);cursor:pointer;
 font:inherit}
.btn-p{background:var(--brand);border-color:var(--brand);color:#fff}
.btn-d{color:var(--danger);border-color:var(--danger)}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:12px;
 background:var(--soft);color:var(--muted)}
.b-ok{background:#e6f4ea;color:var(--ok)}
.b-warn{background:#fff4e5;color:var(--warn)}
.b-danger{background:#fdeaea;color:var(--danger)}
.alert{background:#fff4e5;border-left:3px solid var(--warn);padding:8px 10px;
 border-radius:4px;margin-bottom:10px;font-size:14px}
.alert-ok{background:#e6f4ea;border-left-color:var(--ok)}
.alert-info{background:#e8f1fc;border-left-color:var(--brand)}
.empty{text-align:center;color:var(--muted);padding:28px 10px}
.nav{position:fixed;left:0;right:0;bottom:0;display:flex;background:var(--card);
 border-top:1px solid var(--line);z-index:50}
.nav a{flex:1;text-align:center;padding:7px 0;font-size:11px;color:var(--muted)}
.nav a.on{color:var(--brand);font-weight:600}
.nav span{display:block;font-size:19px;line-height:1.2}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
""" % c


def font_css(level=0):
    """字号三档：0 标准 / 1 小 / 2 大"""
    return ['font-size:15px', 'font-size:13px', 'font-size:17px'][
        level if level in (0, 1, 2) else 0]
