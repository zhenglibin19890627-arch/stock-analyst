#!/bin/bash
# ============================================================
# Stock Analyst 标准化启动脚本 (Linux/Mac)
# 功能：Python检测 → 依赖检查 → 端口检测释放 → 启动 → 健康检查
# ============================================================

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

PORT=5000

echo ""
echo "============================================================"
echo "  Stock Analyst 智能个股分析与评级系统 - 启动脚本"
echo "============================================================"
echo ""

# --- Step 1: Python 检测 ---
echo "[1/5] 检测 Python 环境..."

PYTHON_EXE=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON_EXE="$cmd"
        break
    fi
done

if [ -z "$PYTHON_EXE" ]; then
    echo "  X 未找到 Python，请安装 Python 3.12+"
    echo "  X 启动失败，错误码: 1"
    exit 1
fi

PY_VERSION=$($PYTHON_EXE -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  ✓ 使用 Python: $PYTHON_EXE (v$PY_VERSION)"

# 版本检查
$PYTHON_EXE -c "
import sys
v = sys.version_info
if v.major < 3 or (v.major == 3 and v.minor < 12):
    print('  ! 警告: Python 版本低于 3.12，建议升级')
" || true

# --- Step 2: 依赖检查 ---
echo ""
echo "[2/5] 检查关键依赖..."

DEPS_MISSING=0
for dep in flask pydantic requests; do
    $PYTHON_EXE -c "import $dep" 2>/dev/null
    if [ $? -ne 0 ]; then
        echo "  X 缺失依赖: $dep"
        DEPS_MISSING=1
    else
        echo "  ✓ 已安装: $dep"
    fi
done

if [ "$DEPS_MISSING" = "1" ]; then
    echo ""
    echo "  正在自动安装缺失依赖..."
    $PYTHON_EXE -m pip install -r requirements.txt -q
    if [ $? -ne 0 ]; then
        echo "  X 依赖安装失败，请手动执行: pip install -r requirements.txt"
        echo "  X 启动失败，错误码: 2"
        exit 2
    fi
    echo "  ✓ 依赖安装完成"
fi

# --- Step 3: 端口检测与释放 ---
echo ""
echo "[3/5] 检测端口 $PORT 占用..."

PORT_PID=""
if command -v lsof &>/dev/null; then
    PORT_PID=$(lsof -ti :$PORT 2>/dev/null || true)
elif command -v fuser &>/dev/null; then
    PORT_PID=$(fuser $PORT/tcp 2>/dev/null || true)
fi

if [ -n "$PORT_PID" ]; then
    echo "  ! 端口 $PORT 已被进程 PID=$PORT_PID 占用"
    echo "  正在释放端口..."
    kill -9 $PORT_PID 2>/dev/null || true
    sleep 1
    echo "  ✓ 已终止进程 $PORT_PID，端口已释放"
else
    echo "  ✓ 端口 $PORT 空闲"
fi

# --- Step 4: 数据库 ---
echo ""
echo "[4/5] 初始化数据库..."
if [ ! -f "$PROJECT_DIR/stock_analyst.db" ]; then
    echo "  首次运行，将自动创建数据库"
fi

# --- Step 5: 启动服务 ---
echo ""
echo "[5/5] 启动 Flask 服务..."
echo ""

# 后台启动服务
$PYTHON_EXE "$PROJECT_DIR/app.py" &
SERVER_PID=$!

# 等待服务启动（最多 15 秒）
echo "  等待服务就绪 (PID=$SERVER_PID)..."
WAIT_COUNT=0
MAX_WAIT=15

while [ $WAIT_COUNT -lt $MAX_WAIT ]; do
    sleep 1
    WAIT_COUNT=$((WAIT_COUNT + 1))

    # 健康检查（curl 或 python 回退）
    HEALTH_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/api/health" 2>/dev/null || echo "000")

    if [ "$HEALTH_CODE" = "200" ]; then
        echo ""
        echo "============================================================"
        echo "  [OK] 服务就绪，访问地址：http://127.0.0.1:$PORT"
        echo "  v5.0 评分引擎演示：http://127.0.0.1:$PORT/api/v5/scoring-demo"
        echo "  健康检查：          http://127.0.0.1:$PORT/api/health"
        echo "============================================================"
        echo ""
        echo "  服务进程 PID: $SERVER_PID"
        echo "  按 Ctrl+C 可停止服务"
        echo ""

        # 保持前台运行，等待服务进程
        wait $SERVER_PID
        exit 0
    fi

    # 检查服务进程是否已退出
    if ! kill -0 $SERVER_PID 2>/dev/null; then
        echo ""
        echo "============================================================"
        echo "  [FAIL] 启动失败：服务进程意外退出"
        echo "  错误码: 4 (进程崩溃)"
        echo "============================================================"
        echo ""
        echo "  排查步骤："
        echo "  1. 手动运行诊断: $PYTHON_EXE app.py"
        echo "  2. 检查依赖是否完整: pip install -r requirements.txt"
        echo "  3. 检查数据库文件权限"
        exit 4
    fi
done

# 超时
echo ""
echo "============================================================"
echo "  [FAIL] 启动失败：服务在 $MAX_WAIT 秒内未就绪"
echo "  错误码: 5 (启动超时)"
echo "============================================================"
echo ""
echo "  排查步骤："
echo "  1. 手动运行诊断: $PYTHON_EXE app.py"
echo "  2. 检查端口 $PORT 是否被其他程序占用"
echo "  3. 检查防火墙设置"
exit 5
