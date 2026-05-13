# Power-AIOps 一键启动脚本 (Windows PowerShell)
# 使用方法: 右键点击此文件 -> "使用 PowerShell 运行"
# 或者在终端中执行: .\start.ps1

param(
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Power-AIOps 多智能体编排平台" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查 Python 环境
Write-Host "[1/4] 检查 Python 环境..." -ForegroundColor Yellow
$PythonExe = "D:\anaconda3\envs\agents\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "D:\anaconda3\envs\agents\python.exe"
    if (-not (Test-Path $PythonExe)) {
        Write-Host "  警告: 未找到 agents 环境，使用系统 Python" -ForegroundColor Yellow
        $PythonExe = "python"
    }
}
Write-Host "  使用 Python: $PythonExe" -ForegroundColor Green

# 检查依赖
Write-Host "[2/4] 检查依赖..." -ForegroundColor Yellow
$reqs = @("fastapi", "uvicorn", "httpx")
foreach ($req in $reqs) {
    $installed = & $PythonExe -c "import $req; print('$req')" 2>$null
    if ($null -eq $installed) {
        Write-Host "  缺少依赖: $req，正在安装..." -ForegroundColor Yellow
        & pip install $req --quiet
    } else {
        Write-Host "  $req OK" -ForegroundColor Green
    }
}

# 启动后端服务
Write-Host "[3/4] 启动后端服务 (端口 $Port)..." -ForegroundColor Yellow
$BackendScript = @"
import sys
sys.path.insert(0, '$ProjectRoot')
sys.path.insert(0, '$ProjectRoot\src')

from power_aiops.api.app import app
import uvicorn

uvicorn.run(app, host='127.0.0.1', port=$Port, log_level='info')
"@

$BackendJob = Start-Job -ScriptBlock {
    param($Python, $Script, $Port)
    & $Python -c $Script
} -ArgumentList $PythonExe, $BackendScript, $Port

# 等待服务启动
Write-Host "  等待服务启动..." -ForegroundColor Gray
Start-Sleep -Seconds 3

# 检查服务是否启动成功
$HealthUrl = "http://127.0.0.1:$Port/health"
$MaxRetries = 10
$RetryCount = 0
while ($RetryCount -lt $MaxRetries) {
    try {
        $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            Write-Host "  后端服务启动成功!" -ForegroundColor Green
            break
        }
    } catch {
        $RetryCount++
        Write-Host "  等待中... ($RetryCount/$MaxRetries)" -ForegroundColor Gray
        Start-Sleep -Seconds 2
    }
}

if ($RetryCount -ge $MaxRetries) {
    Write-Host "  警告: 服务可能未正常启动，请检查错误信息" -ForegroundColor Red
}

# 打开浏览器
if (-not $NoBrowser) {
    Write-Host "[4/4] 打开浏览器..." -ForegroundColor Yellow
    Start-Process "http://127.0.0.1:$Port"
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  服务已启动!" -ForegroundColor Green
Write-Host "  前端地址: http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "  API 文档: http://127.0.0.1:$Port/docs" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "按 Ctrl+C 停止服务" -ForegroundColor Gray

# 等待后端进程
try {
    Receive-Job -Job $BackendJob -Wait
} finally {
    Write-Host ""
    Write-Host "正在停止服务..." -ForegroundColor Yellow
    Stop-Job -Job $BackendJob -ErrorAction SilentlyContinue
    Remove-Job -Job $BackendJob -ErrorAction SilentlyContinue
}
