#!/bin/bash
# 全球求职雷达启动脚本
# 每日自动采集 → AI评分 → 深度分析 → 20:30飞书推送

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 激活虚拟环境
source .venv/bin/activate

echo "============================================"
echo "🌐 全球求职雷达启动"
echo "   每日 17:00 采集 → 19:00 评分 → 20:30 推送"
echo "   Web 界面: http://localhost:8077"
echo "   Ctrl+C 停止"
echo "============================================"

# 后台启动 Web 服务
python3 -m uvicorn api.app:app --host 127.0.0.1 --port 8077 --log-level warning &
WEB_PID=$!
echo "✓ Web 界面已启动 (PID: $WEB_PID) → http://localhost:8077"

# 前台运行调度器
python3 main.py

# 调度器退出时同时停止 Web
kill $WEB_PID 2>/dev/null
