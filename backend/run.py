# backend/run.py
import sys
from pathlib import Path
import uvicorn
from backend.main import app

# 將專案根目錄加入 Python 路徑
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )