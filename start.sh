#!/bin/sh
# 使用 -e 選項，讓腳本在任何指令失敗時立即退出
set -e

echo "🚀 啟動腳本開始執行..."

# 1. 使用 envsubst 生成 Nginx 設定檔
# 這個指令會讀取環境變數 $PORT，並將其填入模板中
echo "🔄 正在根據環境變數 \$PORT 生成 Nginx 設定檔..."
envsubst '${PORT}' < /etc/nginx/conf.d/default.template > /etc/nginx/conf.d/default.conf
echo "✅ Nginx 設定檔已生成。Nginx 將監聽埠號: $PORT"
cat /etc/nginx/conf.d/default.conf # 打印出設定檔內容以供偵錯

# 2. 在【背景】啟動後端 Uvicorn 服務
# Uvicorn 在容器內部監聽一個固定的埠號 (例如 8000)
# Nginx 會將流量代理到這個埠號
echo "🚀 正在背景啟動後端 Uvicorn 服務於 0.0.0.0:8000..."
uvicorn backend.main:app --host 0.0.0.0 --port 8000 &

# 等待一小段時間確保後端服務有足夠時間啟動
sleep 5
echo "✅ 後端服務應已啟動"

# 3. 在【前景】啟動 Nginx，並將其作為容器的主進程
# 'daemon off;' 會讓 Nginx 在前景運行，這是容器化部署的標準做法。
# exec 會讓 Nginx 取代 shell 腳本成為 PID 1，能更好地處理來自 Docker 的信號。
echo "🚀 正在前景啟動 Nginx (主進程)..."
exec nginx -g 'daemon off;'