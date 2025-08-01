import logging

def get_logger(name: str):
    """
    獲取一個具名日誌記錄器 (Logger) 的工廠函式。

    此函式是整個應用程式中獲取日誌記錄器的標準方式。
    它不進行任何配置，而是直接返回一個由 Python 標準 `logging` 模組管理的記錄器實例。
    所有日誌的格式、等級和輸出目標等配置，都由應用程式啟動時的 `setup_logging` 函式統一完成。
    :param name: 日誌記錄器的名稱，通常傳入 `__name__` 以便追蹤日誌來源模組。
    :return: 一個 `logging.Logger` 的實例。
    """
    return logging.getLogger(name)
