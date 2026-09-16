# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 公式词法分析（Tokenizer）

把 '=SUM(A1:B2)*1.5&"元"' 拆成一串 token，交给 sp_parser 建语法树。
只认 Excel 的写法，不认任何 Python 语法——用户填的公式可能来自导入的
Excel 文件，绝不能给它执行任意代码的能力。

Token 类型
    NUM     数字（含 1.5e3、50%）
    STR     字符串 "abc"，里面 "" 表示一个引号
    REF     单元格/区域/名称引用 A1, $A$1, A1:B2, Sheet1!A1, 单价
    FUNC    函数名后紧跟左括号，如 SUM(
    BOOL    TRUE / FALSE（也认 真 / 假）
    ERR     错误字面量 #N/A、#DIV/0! 等
    OP      运算符
    LP RP   括号
    COMMA   参数分隔 ,（也认全角 ，）
    COLON   区域冒号 :
    SEMI    并集空格运算符的替代 ;（部分习惯用 ; 分隔参数）
"""

# 错误字面量（Excel 的七种）
ERRORS = ('#NULL!', '#DIV/0!', '#VALUE!', '#REF!', '#NAME?', '#NUM!',
          '#N/A', '#GETTING_DATA', '#CIRC!', '#SPILL!')

# 运算符，按长度从长到短匹配
_OPS = ('<=', '>=', '<>', '+=', '-=', '*=', '/=', '^=', '&=',
        '+', '-', '*', '/', '^', '&', '=', '<', '>', '%')


class Tok(object):
    __slots__ = ('kind', 'val', 'pos')

    def __init__(self, kind, val, pos=0):
        self.kind = kind
        self.val = val
        self.pos = pos

    def __repr__(self):
        return '<%s %r>' % (self.kind, self.val)


class LexError(Exception):
    """词法错误：公式写错了，比如引号没闭合"""


def _is_name_start(ch):
    return ch.isalpha() or ch == '_' or ch == '\\' or '\u4e00' <= ch <= '\u9fff'


def _is_name_char(ch):
    return (ch.isalnum() or ch in '_.' or ch == '\\'
            or '\u4e00' <= ch <= '\u9fff')


def tokenize(src):
    """把公式源码拆成 token 列表。开头的 '=' 可有可无"""
    s = (src or '').strip()
    if s.startswith('='):
        s = s[1:]
    out = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]

        # 空白（并集运算符在 parser 里处理，这里直接跳过）
        if ch in ' \t\r\n':
            i += 1
            continue

        # 全角逗号/冒号/括号归一
        if ch in '，':
            out.append(Tok('COMMA', ',', i)); i += 1; continue
        if ch in '：':
            out.append(Tok('COLON', ':', i)); i += 1; continue
        if ch in '（':
            out.append(Tok('LP', '(', i)); i += 1; continue
        if ch in '）':
            out.append(Tok('RP', ')', i)); i += 1; continue
        if ch in '；':
            out.append(Tok('SEMI', ';', i)); i += 1; continue

        # 字符串
        if ch == '"':
            j = i + 1
            buf = []
            closed = False
            while j < n:
                if s[j] == '"':
                    if j + 1 < n and s[j + 1] == '"':   # "" → 一个引号
                        buf.append('"'); j += 2; continue
                    j += 1; closed = True; break
                buf.append(s[j]); j += 1
            if not closed:
                raise LexError('字符串没有闭合的引号')
            out.append(Tok('STR', ''.join(buf), i))
            i = j
            continue

        # 错误字面量
        up = s[i:].upper()
        hit = None
        for e in ('#DIV/0!', '#NULL!', '#VALUE!', '#REF!', '#NAME?', '#NUM!',
                  '#N/A', '#CIRC!', '#SPILL!', '#GETTING_DATA'):
            if up.startswith(e):
                hit = e
                break
        if hit:
            out.append(Tok('ERR', hit, i)); i += len(hit); continue

        # 数字（含 .5、1e3、50%）
        if ch.isdigit() or (ch == '.' and i + 1 < n and s[i + 1].isdigit()):
            j = i
            dot = 0
            while j < n and (s[j].isdigit() or (s[j] == '.' and dot == 0
                                                and (j == i or s[j - 1] != '%'))):
                if s[j] == '.':
                    dot += 1
                j += 1
            # 科学计数法
            if j < n and s[j] in 'eE':
                k = j + 1
                if k < n and s[k] in '+-':
                    k += 1
                if k < n and s[k].isdigit():
                    while k < n and s[k].isdigit():
                        k += 1
                    j = k
            txt = s[i:j]
            if j < n and s[j] == '%':
                out.append(Tok('NUM', _num(txt) / 100.0, i))
                out.append(Tok('OP', '%', j))
                i = j + 1
                continue
            if j < n and s[j] == '%':
                i = j
                continue
            out.append(Tok('NUM', _num(txt), i))
            i = j
            continue

        # 名称 / 函数名 / 引用
        if _is_name_start(ch):
            j = i
            while j < n and _is_name_char(s[j]):
                j += 1
            word = s[i:j]

            # 单引号包裹的表名  '我的表'!A1
            if ch == "'" or (i > 0 and False):
                pass
            k = j
            if k < n and s[k] == "'":                 # 表名含空格
                m = s.find("'", k + 1)
                if m > 0 and m + 1 < n and s[m + 1] == '!':
                    word = s[i:m + 1]
                    j = m + 1

            # 后面紧跟 '(' → 函数调用
            k = j
            while k < n and s[k] in ' \t':
                k += 1
            if k < n and s[k] == '(':
                out.append(Tok('FUNC', word.upper(), i))
                i = k
                continue

            # 后面是 '!' → 跨表引用，连表名一起吞掉
            if j < n and s[j] == '!':
                j += 1
                # 区域两端都要带上表名：Sheet1!A1:B2
                rest = s[j:]
                mm = _REF_REST_RE.match(rest)
                if mm:
                    word = s[i:j + mm.end()]
                    j = j + mm.end()
                else:
                    word = s[i:j]
                out.append(Tok('REF', word, i))
                i = j
                continue

            uw = word.upper()
            if word in ('TRUE', 'FALSE', '真', '假'):
                out.append(Tok('BOOL', word in ('TRUE', '真'), i))
                i = j
                continue

            out.append(Tok('REF', word, i))
            i = j
            continue

        # 单引号表名开头（'我的表'!A1）
        if ch == "'":
            m = s.find("'", i + 1)
            if m > 0 and m + 1 < n and s[m + 1] == '!':
                j = m + 2
                mm = _REF_REST_RE.match(s[j:])
                if mm:
                    j += mm.end()
                word = s[i:j]
                k = j
                while k < n and s[k] in ' \t':
                    k += 1
                if k < n and s[k] == '(':
                    out.append(Tok('FUNC', word.upper(), i)); i = k; continue
                out.append(Tok('REF', word, i))
                i = j
                continue
            raise LexError("单引号没有配对")

        # 运算符
        for op in _OPS:
            if s.startswith(op, i):
                # 单独一个 % 出现在数字后面已经在上面处理了
                out.append(Tok('OP', op, i))
                i += len(op)
                break
        else:
            if ch == ',':
                out.append(Tok('COMMA', ',', i)); i += 1; continue
            if ch == ';':
                out.append(Tok('SEMI', ';', i)); i += 1; continue
            if ch == ':':
                out.append(Tok('COLON', ':', i)); i += 1; continue
            if ch == '(':
                out.append(Tok('LP', '(', i)); i += 1; continue
            if ch == ')':
                out.append(Tok('RP', ')', i)); i += 1; continue
            if ch == '{':                       # 数组常量 {1,2;3,4}
                depth, j = 1, i + 1
                while j < n and depth:
                    if s[j] == '{':
                        depth += 1
                    elif s[j] == '}':
                        depth -= 1
                    j += 1
                if depth:
                    raise LexError('数组常量没有闭合的 }')
                out.append(Tok('ARRAY', s[i + 1:j - 1], i))
                i = j
                continue
            raise LexError('认不出的字符 %r（位置 %d）' % (ch, i))
    return out


def _num(t):
    try:
        return float(t)
    except ValueError:
        return 0.0


import re as _re
# Sheet1!A1:B2 里 '!' 之后的部分
_REF_REST_RE = _re.compile(
    r'\$?[A-Za-z]{1,3}\$?[0-9]{1,7}(:\$?[A-Za-z]{1,3}\$?[0-9]{1,7})?'
    r'|\$?[A-Za-z]{1,3}:\$?[A-Za-z]{1,3}'
    r'|\$?[0-9]{1,7}:\$?[0-9]{1,7}'
    r'|[^\W\d][\w\u4e00-\u9fff\.]*', _re.UNICODE)
