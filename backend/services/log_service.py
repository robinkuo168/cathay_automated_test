import json
import threading
from typing import List, Dict
from datetime import datetime
from .logger import get_logger

class LogService:
    def __init__(self, max_logs: int = 1000):
        """
        初始化 LogService。

        此建構函式會設定一個用於在記憶體中儲存日誌的列表、
        最大日誌數量的限制，以及一個執行緒鎖 (lock) 來確保多執行緒環境下的操作安全。
        :param max_logs: 要在記憶體中保留的最大日誌條數。
        """
        self.logs = []
        self.max_logs = max_logs
        self._lock = threading.Lock()
        self.logger = get_logger(__name__)

    def add_log(self, level: str, message: str, extra_data: Dict = None):
        """
        添加一條新的日誌記錄到記憶體中，並同時寫入到系統的標準日誌。

        此函式是執行緒安全的。它會創建一個包含時間戳、級別和訊息的結構化日誌條目，
        並在日誌數量超過上限時，自動移除最舊的記錄以維持列表大小。
        :param level: 日誌的嚴重性級別 (例如 "INFO", "ERROR")。
        :param message: 日誌的訊息內容。
        :param extra_data: (可選) 一個字典，用於附加任何額外的結構化資訊。
        """
        try:
            with self._lock:
                log_entry = {
                    "id": len(self.logs) + 1,
                    "timestamp": datetime.now().isoformat(),
                    "level": level.upper(),
                    "message": str(message),
                    "extra_data": extra_data or {}
                }

                self.logs.append(log_entry)

                # 保持日誌數量限制
                if len(self.logs) > self.max_logs:
                    self.logs = self.logs[-self.max_logs:]

                # 同時記錄到系統日誌
                if level.upper() == 'ERROR':
                    self.logger.error(message)
                elif level.upper() == 'WARNING':
                    self.logger.warning(message)
                else:
                    self.logger.info(message)

        except Exception as e:
            self.logger.error(f"添加日誌失敗: {e}")

    def get_logs(self, limit: int = 50, level_filter: str = None) -> List[Dict]:
        """
        從記憶體中獲取日誌記錄。

        此函式可以根據指定的數量限制和嚴重性級別來過濾日誌，並總是返回最新的記錄。
        :param limit: 要返回的最大日誌條數。
        :param level_filter: (可選) 用於過濾日誌的級別字串 (例如 "ERROR")。
        :return: 一個包含日誌條目字典的列表。
        """
        try:
            with self._lock:
                logs = self.logs.copy()

            # 按級別過濾
            if level_filter:
                logs = [log for log in logs if log['level'] == level_filter.upper()]

            # 返回最新的記錄
            return logs[-limit:] if limit > 0 else logs

        except Exception as e:
            self.logger.error(f"獲取日誌失敗: {e}")
            return []

    def clear_logs(self):
        """
        清空所有儲存在記憶體中的日誌記錄。

        這是一個執行緒安全的操作，用於重置日誌服務的狀態。
        """
        try:
            with self._lock:
                cleared_count = len(self.logs)
                self.logs = []
                self.logger.info(f"已清空 {cleared_count} 條日誌記錄")
        except Exception as e:
            self.logger.error(f"清空日誌失敗: {e}")

    def get_log_statistics(self) -> Dict:
        """
        計算並獲取關於當前記憶體中日誌的統計資訊。

        :return: 一個包含總數、按級別分類的計數、最近的錯誤訊息等統計數據的字典。
        """
        try:
            with self._lock:
                logs = self.logs.copy()

            if not logs:
                return {
                    "total": 0,
                    "by_level": {},
                    "recent_errors": [],
                    "oldest_timestamp": None,
                    "newest_timestamp": None
                }

            # 按級別統計
            level_counts = {}
            recent_errors = []

            for log in logs:
                level = log['level']
                level_counts[level] = level_counts.get(level, 0) + 1

                # 收集最近的錯誤
                if level == 'ERROR' and len(recent_errors) < 10:
                    recent_errors.append({
                        "timestamp": log['timestamp'],
                        "message": log['message']
                    })

            return {
                "total": len(logs),
                "by_level": level_counts,
                "recent_errors": recent_errors[-10:],  # 最近10個錯誤
                "oldest_timestamp": logs[0]['timestamp'] if logs else None,
                "newest_timestamp": logs[-1]['timestamp'] if logs else None
            }

        except Exception as e:
            self.logger.error(f"獲取日誌統計失敗: {e}")
            return {"error": str(e)}

    def export_logs(self, format_type: str = "json") -> str:
        """
        將所有儲存在記憶體中的日誌匯出為指定格式的字串。

        :param format_type: 想要的匯出格式，支援 "json" 或 "csv"。
        :return: 一個包含所有日誌內容的字串。
        :raises ValueError: 如果請求了不支援的匯出格式。
        """
        try:
            with self._lock:
                logs = self.logs.copy()

            if format_type.lower() == "json":
                return json.dumps(logs, ensure_ascii=False, indent=2)
            elif format_type.lower() == "csv":
                if not logs:
                    return "timestamp,level,message\n"

                lines = ["timestamp,level,message"]
                for log in logs:
                    # 簡單的 CSV 轉義
                    message = log['message'].replace('"', '""')
                    lines.append(f'"{log["timestamp"]}","{log["level"]}","{message}"')

                return '\n'.join(lines)
            else:
                raise ValueError(f"不支援的匯出格式: {format_type}")

        except Exception as e:
            self.logger.error(f"匯出日誌失敗: {e}")
            return f"匯出失敗: {str(e)}"