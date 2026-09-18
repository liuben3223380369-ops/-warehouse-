# -*- coding: utf-8 -*-
"""采购单 · 共享辅助

    汇总快照同步（首页 / 详情 / 到货 / 付款 都用它）
"""
from .. import summary
from ...core.util import _log_err


def _po_sync(po_id):
    """采购单任何变动后重算汇总快照。失败不阻断主流程，只记日志。"""
    try:
        summary.save_summary(po_id)
    except Exception:
        _log_err('采购汇总保存失败 po_id=%s' % po_id)
