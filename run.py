"""PyCharm / IDE friendly entry point: right click -> Run 'run'.

Also works from the terminal:  python run.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

if __name__ == "__main__":
    if not os.getenv("IMAGE_API_KEY"):
        print("[warn] IMAGE_API_KEY 未配置，请复制 .env.example 为 .env 并填入 Key", flush=True)
    port = int(os.getenv("PORT", "8000"))
    print(f"Demo: http://127.0.0.1:{port}", flush=True)
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=False, app_dir=str(ROOT))
