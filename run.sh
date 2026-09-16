#!/usr/bin/env bash
# 单人仓库管理系统 启动脚本（Termux / Linux 通用）
cd "$(dirname "$0")" || exit 1

PORT=${PORT:-8080}
export WAREHOUSE_DB=${WAREHOUSE_DB:-"$PWD/warehouse.db"}

command -v python3 >/dev/null || { echo "未找到 python3，请先安装: pkg install python"; exit 1; }
python3 -c "import flask" 2>/dev/null || { echo "正在安装依赖..."; pip install -r requirements.txt || exit 1; }

# 启动前自动备份一份（保留最近 10 份），出问题可回滚
python3 -c "
import sys; sys.path.insert(0,'.')
from wh.core import db; db.init()
p = db.backup()
print('已自动备份:', p.split('/')[-1])
" 2>/dev/null

IP=$(ip addr show 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | grep -v '^127' | head -1)
echo "---------------------------------------------"
echo " 仓库管理系统"
echo " 手机本机访问:  http://127.0.0.1:$PORT"
[ -n "$IP" ] && echo " 同局域网访问:  http://$IP:$PORT"
echo " 数据库文件:    $WAREHOUSE_DB"
echo " 停止服务:      Ctrl+C"
echo "---------------------------------------------"

exec python3 run.py "$PORT"
