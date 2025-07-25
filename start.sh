#!/bin/sh
# 使用 -e 選項，讓腳本在任何指令失敗時立即退出
set -e

echo "🚀 啟動腳本開始執行..."

# 1. 使用 envsubst 生成 Nginx 設定檔 (這部分是正確的)
# 假設您的模板中使用了 ${PORT} 變數
envsubst '${PORT}' < /etc/nginx/conf.d/default.template > /etc/nginx/conf.d/default.conf
echo "✅ Nginx 設定檔已生成至 /etc/nginx/conf.d/default.conf"

# 2. 在【背景】啟動後端 Uvicorn 服務
echo "🚀 正在背景啟動後端 Uvicorn 服務..."
uvicorn backend.main:app --host 0.0.0.0 --port 8000 &

# 等待一小段時間確保後端服務有足夠時間啟動 (可選，但有助於穩定性)
sleep 3
echo "✅ 後端服務應已啟動"

# 3. 在【前景】啟動 Nginx，並將其作為容器的主進程
# 'daemon off;' 會讓 Nginx 在前景運行，這是容器化部署的標準做法。
# 這是腳本的最後一個命令，它會"卡住"在這裡，從而保持容器運行。
# 使用 exec 是最佳實踐，它讓 Nginx 成為 PID 1，能更好地處理來自 Docker 的信號 (如 SIGTERM)。
echo "🚀 正在前景啟動 Nginx (主進程)..."
exec nginx -g 'daemon off;'