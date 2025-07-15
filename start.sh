#!/bin/sh
set -e

# 將 Nginx 設定檔中的 ${PORT} 替換成 Code Engine 提供的實際 port
envsubst '${PORT}' < /etc/nginx/conf.d/default.template > /etc/nginx/conf.d/default.conf

# 在背景啟動 Nginx
nginx -g 'daemon off;' &

# 啟動後端應用程式
exec uvicorn backend.main:app --host 0.0.0.0 --port 8000