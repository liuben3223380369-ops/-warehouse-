@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
REM ============================================
REM  仓库管理系统 —— Windows 一键打包成 exe
REM  双击运行即可，产物在 dist\仓库管理系统.exe
REM ============================================

echo.
echo   ============================================
echo    仓库管理系统  打包工具
echo   ============================================
echo.

REM 依次尝试 py 启动器 / python / python3，谁在就用谁
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py"
if not defined PYCMD (where python >nul 2>nul && set "PYCMD=python")
if not defined PYCMD (where python3 >nul 2>nul && set "PYCMD=python3")

if not defined PYCMD (
    echo   [x] 没找到 Python
    echo.
    echo   请先到 https://www.python.org/downloads/ 下载安装
    echo   安装时务必勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

echo   [1/5] 使用解释器: %PYCMD%
%PYCMD% --version
if errorlevel 1 (
    echo   [x] 解释器无法执行
    pause
    exit /b 1
)

REM 切到 bat 所在目录，避免从别处双击时路径不对
cd /d "%~dp0"
echo   当前目录: %CD%
echo.

echo   [2/6] 安装核心依赖...
%PYCMD% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo.
    echo   [x] 依赖安装失败，检查网络后重试
    echo      国内可加镜像重试:
    echo      pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt pyinstaller
    pause
    exit /b 1
)

echo.
echo   [3/6] 安装独立窗口依赖(Qt WebEngine,约 400MB,可选)...
%PYCMD% -m pip install -r requirements-desktop.txt
if errorlevel 1 (
    echo   [!] Qt 没装上,程序仍能打包,但会退回浏览器打开
    echo      想用独立窗口请手动执行: %PYCMD% -m pip install PySide6
)

echo.
echo   [4/6] 清理旧产物...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo   [5/6] 正在打包，请耐心等待 3~10 分钟(Qt 较大)...
%PYCMD% -m PyInstaller warehouse.spec --noconfirm --clean
if errorlevel 1 (
    echo.
    echo   [x] 打包失败，请往上翻看错误信息
    pause
    exit /b 1
)

echo.
echo   [6/6] 检查产物...
if not exist "dist\仓库管理系统\仓库管理系统.exe" (
    echo   [x] 没找到 dist\仓库管理系统\仓库管理系统.exe
    echo      dist 目录内容：
    dir /b dist 2>nul
    pause
    exit /b 1
)

echo.
echo   ============================================
echo    打包完成
echo.
echo    程序目录:  dist\仓库管理系统\
echo    主程序:    仓库管理系统.exe
echo    双击 exe 会打开独立窗口（不是浏览器）
echo    数据保存在 exe 旁边的 warehouse.db，别删
echo    注意: _internal 文件夹是运行必需的，不要删、不要单独拷走 exe
echo   ============================================
echo.
if exist dist explorer dist
pause