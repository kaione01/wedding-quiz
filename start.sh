#!/bin/bash
# 婚禮問答遊戲啟動腳本
cd "$(dirname "$0")"

echo "======================================"
echo "🎮 婚禮問答遊戲系統"
echo "======================================"

# 安裝套件
pip install -r requirements.txt -q

# 防止 Mac 睡眠
caffeinate -d &

echo ""
echo "📺 大螢幕：  http://localhost:8001/display"
echo "📱 玩家加入：http://localhost:8001/play"
echo "🎛️  MC 控制：  http://localhost:8001/host"
echo ""
echo "按 Ctrl+C 停止"
echo "======================================"

python main.py
