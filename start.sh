#!/bin/bash
# Power-AIOps 一键启动脚本 (Linux/macOS)
# 使用方法: ./start.sh

set -e

# 颜色定义
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  Power-AIOps 多智能体编排平台${NC}"
echo -e "${CYAN}========================================${NC}"
echo ""

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}/src:${SCRIPT_DIR}"

# 查找 Python
echo -e "${YELLOW}[1/3] 检查 Python 环境...${NC}"
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo "错误: 未找到 Python"
    exit 1
fi
echo -e "${GREEN}  使用 Python: ${PYTHON_CMD}${NC}"

# 检查依赖
echo -e "${YELLOW}[2/3] 检查依赖...${NC}"
for pkg in fastapi uvicorn httpx; do
    if $PYTHON_CMD -c "import $pkg" 2>/dev/null; then
        echo -e "${GREEN}  $pkg OK${NC}"
    else
        echo -e "${YELLOW}  缺少 $pkg，正在安装...${NC}"
        pip install $pkg --quiet
    fi
done

# 启动服务
echo -e "${YELLOW}[3/3] 启动服务...${NC}"
echo ""
echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  服务地址: http://127.0.0.1:8000${NC}"
echo -e "${CYAN}  API 文档: http://127.0.0.1:8000/docs${NC}"
echo -e "${CYAN}========================================${NC}"
echo ""
echo -e "按 Ctrl+C 停止服务"
echo ""

# 打开浏览器 (macOS)
if [[ "$OSTYPE" == "darwin"* ]]; then
    sleep 1 && open http://127.0.0.1:8000 &
fi

# 启动 FastAPI
cd "$SCRIPT_DIR"
$PYTHON_CMD -c "import sys; sys.path.insert(0, 'src'); from power_aiops.api.app import app; import uvicorn; uvicorn.run(app, host='127.0.0.1', port=8000)"
