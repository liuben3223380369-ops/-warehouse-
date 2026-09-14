#!/usr/bin/env bash
# ============================================
#  上传到 GitHub：liuben3223380369
#  用法：bash 上传到GitHub.sh
# ============================================
set -e
REPO="liuben3223380369/仓库管理系统"

cd "$(dirname "$0")" || exit 1

echo "============================================"
echo " 上传到 GitHub"
echo " 仓库: $REPO"
echo "============================================"
echo
echo " 前置条件（只需做一次）："
echo "  1. 在 github.com/new 建一个空仓库，名字随便（不要用中文也行）"
echo "  2. 生成 token: github.com → Settings → Developer settings"
echo "     → Personal access tokens → Tokens (classic) → Generate new token"
echo "     → 勾选 repo（全部）→ 生成后复制那串 ghp_ 开头的字符"
echo
echo " 推送时会问密码，把 token 粘进去（屏幕不显示，粘完直接回车）"
echo "============================================"
echo

# 没提交过的先提交
if [ -n "$(git status --porcelain)" ]; then
    echo "[1/3] 有改动，先提交..."
    git add -A
    git commit -m "更新仓库管理系统 $(date '+%Y-%m-%d %H:%M')" || true
else
    echo "[1/3] 没有待提交的改动"
fi

git branch -M main 2>/dev/null || true

echo "[2/3] 设置远程地址..."
if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "https://github.com/$REPO.git"
else
    git remote add origin "https://github.com/$REPO.git"
fi
echo "       origin = https://github.com/$REPO.git"

echo "[3/3] 推送..."
git push -u origin main

echo
echo "============================================"
echo " 推送完成！"
echo " 打开看看: https://github.com/$REPO"
echo
echo " 想让 GitHub 自动帮你打包 Windows exe？"
echo "   git tag v1.0 && git push origin v1.0"
echo " 然后在仓库页面的 Actions 里等几分钟，"
echo " Releases 里就能下载 exe 了。"
echo "============================================"
