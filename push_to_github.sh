#!/bin/bash
# 一鍵初始化並推送到 GitHub
# 執行前請先到 https://github.com/new 建立 repo 名稱 "wedding-quiz"
# 然後在 Mac 終端機執行：bash push_to_github.sh

cd "$(dirname "$0")"

echo "🚀 初始化 Git..."
git init
git add .
git commit -m "feat: 婚禮問答遊戲系統初版

- FastAPI + WebSocket 後端 (port 8001)
- display.html 大螢幕畫面
- play.html 手機答題畫面
- host.html MC 控制台
- 速度計分、排行榜、揭曉媒體支援
- LINE 關鍵字回覆遊戲連結"

echo ""
echo "🔗 設定遠端..."
git remote add origin https://github.com/kaione01/wedding-quiz.git

echo "⬆️  推送到 GitHub..."
git branch -M main
git push -u origin main

echo ""
echo "✅ 完成！"
echo "Repo：https://github.com/kaione01/wedding-quiz"
