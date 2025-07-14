import logging

def get_logger(name: str):
    """
    僅僅獲取一個 logger 實例。
    所有設定都由應用程式入口的集中設定完成。
    """
    return logging.getLogger(name)
