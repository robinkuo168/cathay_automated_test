# ---- 使用 Python 映像檔作為最終基礎 ----
FROM python:3.12-slim

# 設定工作目錄
WORKDIR /app

# ---- 準備環境 ----
# 安裝 Nginx 和 gettext (用於 envsubst 指令)
RUN apt-get update && apt-get install -y nginx gettext && rm -rf /var/lib/apt/lists/*

# ---- 準備後端 ----
# 複製後端依賴需求檔案並安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製後端程式碼
COPY backend/ ./backend/

# ---- 準備前端 ----
# 直接複製整個 frontend 目錄的內容到 Nginx 的網站根目錄
COPY frontend/ /var/www/html/

# ---- 整合與啟動 ----
# 複製 Nginx 設定檔模板和啟動腳本
COPY nginx.conf /etc/nginx/conf.d/default.template
COPY start.sh .

# 給予啟動腳本執行權限
RUN chmod +x ./start.sh

# 設定容器的啟動命令
ENTRYPOINT ["./start.sh"]