@echo off
chcp 65001 >nul
REM ============================================
REM  仓库管理系统 —— Windows 一键打包成 exe
REM  双击运行即可，产物在 dist\仓库管理系统.exe
REM ============================================

echo.
echo   ============================================
echo    仓库管理系统  打包工具
echo   ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo   [x] 没找到 Python
    echo.
    echo   请先到 https://www.python.org/downloads/ 下载安装
    echo   安装时务必勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

echo   [1/4] 检查 Python...
python --version

echo   [2/4] 安装打包依赖...
python -m pip install -r requirements.txt pyinstaller -q
if errorlevel 1 (
    echo   [x] 依赖安装失败，检查网络后重试
    pause
    exit /b 1
)

echo   [3/4] 清理旧产物...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo   [4/4] 正在打包，请耐心等待 1~3 分钟...
python -m PyInstaller warehouse.spec --noconfirm --clean
if errorlevel 1 (
    echo.
    echo   [x] 打包失败，请往上翻看错误信息
    pause
    exit /b 1
)

echo.
echo   ============================================
echo    打包完成
echo.
echo    程序位置:  dist\仓库管理系统.exe
echo    双击 exe 会自动打开浏览器
echo    数据保存在 exe 旁边的 warehouse.db，别删
echo   ============================================
echo.
if exist dist explorer dist
pause
