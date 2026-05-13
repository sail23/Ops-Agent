@echo off
chcp 65001 >nul
echo ========================================
echo   Power-AIOps 多智能体编排平台
echo ========================================
echo.

REM 设置 Python 路径
set PYTHON_EXE=D:\anaconda3\envs\agents\python.exe
set PROJECT_ROOT=%~dp0
set PYTHONPATH=%PROJECT_ROOT%src;%PROJECT_ROOT%

REM 检查 Python 是否存在
if not exist "%PYTHON_EXE%" (
    echo 警告: 未找到 agents 环境的 Python，使用系统 Python
    set PYTHON_EXE=python
)

echo [1/3] 使用 Python: %PYTHON_EXE%

REM 检查依赖
echo [2/3] 检查依赖...
%PYTHON_EXE% -c "import fastapi" 2>nul || pip install fastapi --quiet
%PYTHON_EXE% -c "import uvicorn" 2>nul || pip install uvicorn --quiet
%PYTHON_EXE% -c "import httpx" 2>nul || pip install httpx --quiet
echo 依赖检查完成

REM 启动服务
echo [3/3] 启动服务...
echo.
echo ========================================
echo   服务地址: http://127.0.0.1:8000
echo   API 文档: http://127.0.0.1:8000/docs
echo ========================================
echo.
echo 按 Ctrl+C 停止服务
echo.

cd /d "%PROJECT_ROOT%"
start http://127.0.0.1:8000
%PYTHON_EXE% -c "import sys; sys.path.insert(0, 'src'); from power_aiops.api.app import app; import uvicorn; uvicorn.run(app, host='127.0.0.1', port=8000, log_level='info')"
