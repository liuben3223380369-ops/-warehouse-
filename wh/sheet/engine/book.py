# -*- coding: utf-8 -*-
"""工作簿 —— 一本 = 多张 Sheet + 命名区域

只管装载与寻址，不管怎么算。
"""
from ..kernel import addr as A
from .sheet import Sheet


class Workbook(object):
    def __init__(self, name='工作簿1'):
        self.name = name
        self.sheets = []          # [Sheet]
        self.names = {}           # 命名区域：名称 -> 'Sheet1!A1:B2'
        self.active = 0

    # ---------------- 表管理 ----------------
    def add(self, name=None, rows=200, cols=26):
        name = name or self._next_name()
        s = Sheet(name, rows, cols)
        s.book = self
        self.sheets.append(s)
        return s

    def _next_name(self):
        i = len(self.sheets) + 1
        while any(s.name == 'Sheet%d' % i for s in self.sheets):
            i += 1
        return 'Sheet%d' % i

    def sheet(self, name):
        if isinstance(name, int):
            return self.sheets[name] if 0 <= name < len(self.sheets) else None
        n = str(name).strip().lower()
        for s in self.sheets:
            if s.name.strip().lower() == n:
                return s
        return None

    @property
    def act(self):
        if not self.sheets:
            self.add('Sheet1')
        if self.active >= len(self.sheets):
            self.active = 0
        return self.sheets[self.active]

    def rename(self, old, new):
        s = self.sheet(old)
        if not s or not new or self.sheet(new):
            return False
        s.name = new
        for k, v in list(self.names.items()):
            if str(v).split('!')[0].lower() == str(old).lower():
                self.names[k] = new + '!' + str(v).split('!', 1)[1]
        for sh in self.sheets:
            sh.invalidate()
        return True

    def remove(self, name):
        s = self.sheet(name)
        if not s or len(self.sheets) <= 1:
            return False
        self.sheets.remove(s)
        self.active = min(self.active, len(self.sheets) - 1)
        return True

    def order(self, names):
        """调整表顺序"""
        mp = {s.name: s for s in self.sheets}
        out = [mp[n] for n in names if n in mp]
        for s in self.sheets:
            if s not in out:
                out.append(s)
        self.sheets = out

    # ---------------- 命名区域 ----------------
    def define_name(self, name, ref):
        if not name or not ref:
            return False
        self.names[str(name).strip()] = str(ref).strip()
        for s in self.sheets:
            s.invalidate()
        return True

    def del_name(self, name):
        return self.names.pop(str(name).strip(), None) is not None

    def value_of_name(self, name):
        ref = self.names.get(name) or self.names.get(name.lower())
        if not ref:
            return '#NAME?'
        r = A.parse_ref(ref)
        if r is None:
            return '#NAME?'
        return self.act._eval_ref(r, 0, 0, 0)

    def resolve(self, ref_text):
        """把 'Sheet1!A1' / 'A1:B2' / 命名 解析成 (sheet, Ref)"""
        r = A.parse_ref(ref_text)
        if r is None:
            return None, None
        sh = self.sheet(r.sheet) if r.sheet else self.act
        return sh, r

    # ---------------- 序列化 ----------------
    def to_dict(self):
        return {'name': self.name, 'active': self.active,
                'names': self.names,
                'sheets': [s.to_dict() for s in self.sheets]}

    @staticmethod
    def from_dict(d):
        b = Workbook(d.get('name') or '工作簿1')
        b.names = dict(d.get('names') or {})
        for sd in (d.get('sheets') or []):
            b.sheets.append(Sheet.from_dict(sd, b))
        if not b.sheets:
            b.add('Sheet1')
        b.active = min(int(d.get('active') or 0), len(b.sheets) - 1)
        return b

    # ---------------- 便捷 ----------------
    def set(self, addr, text, sheet=None):
        sh = self.sheet(sheet) if sheet else self.act
        r = A.parse_ref(addr)
        if r is None or r.kind != 'cell':
            return False
        sh.set_raw(r.r1, r.c1, text)
        return True

    def val(self, addr, sheet=None):
        sh = self.sheet(sheet) if sheet else self.act
        r = A.parse_ref(addr)
        if r is None or r.kind != 'cell':
            return None
        return sh.value(r.r1, r.c1)

