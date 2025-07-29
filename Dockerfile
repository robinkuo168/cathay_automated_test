# ---- 使用 Python 映像檔作為最終基礎 ----
FROM python:3.12-slim

# 設定工作目錄
WORKDIR /app

# ---- 準備環境 ----
# 安裝 Nginx 和 gettext (用於 envsubst 指令)
RUN apt-get update && apt-get install -y nginx gettext && rm -rf /var/lib/apt/lists/*
# 建立一個非 root 使用者和群組來運行應用程式
RUN groupadd -r appuser && useradd --no-log-init -r -g appuser appuser

# 建立相關目錄，並將它們的擁有者變更為 'appuser'，以避免啟動時發生權限錯誤。
RUN mkdir -p /var/lib/nginx/body /var/log/nginx && \
    touch /var/run/nginx.pid && \
    chown -R appuser:appuser /var/lib/nginx /var/log/nginx /var/run/nginx.pid

# ---- 準備後端 ----
# 複製後端依賴需求檔案並安裝
COPY requirements.txt .
RUN pip install --default-timeout=100 --no-cache-dir -r requirements.txt

# 複製後端程式碼
COPY backend/ ./backend/

# ---- 準備憑證 ----
COPY certs/ ./certs/

# ---- 準備前端 ----
# 直接複製整個 frontend 目錄的內容到 Nginx 的網站根目錄
COPY frontend/ /var/www/html/

# ---- 整合與啟動 ----
# 複製 Nginx 設定檔模板和啟動腳本
COPY nginx.main.conf /etc/nginx/nginx.conf
COPY nginx.conf /etc/nginx/conf.d/default.template
COPY start.sh .

# 給予啟動腳本執行權限
RUN chmod +x ./start.sh

# 將工作目錄的所有權變更為新使用者
RUN chown -R appuser:appuser /app

# 將 /app 目錄、Nginx 設定檔、以及網站內容的擁有者都變更為 appuser。
# 同時，使用 chmod -R 755 確保網站目錄 (/var/www/html) 具有正確的讀取和執行權限。
RUN touch /etc/nginx/conf.d/default.conf && \
    chown -R appuser:appuser /app /etc/nginx/conf.d/default.conf /var/www/html && \
    chmod -R 755 /var/www/html

# 切換到非 root 使用者
USER appuser

# 設定容器的啟動命令
ENTRYPOINT ["./start.sh"]