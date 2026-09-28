"""Local demo without Docker: python scripts/dev.py (requires dependencies)."""

import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from app.config import load_settings
from app.main import create_app
from app.demo import create_demo_app

os.environ.setdefault("DASHBOARD_PASSWORD", "local-demo-2026!")
cfg = load_settings("config/demo.toml")
for key in ("github", "confluence", "llm"):
    cfg[key]["api_url"] = cfg[key]["api_url"].replace("fixtures:9001", "127.0.0.1:9001")
cfg["storage"]["path"] = "data/local-demo.sqlite3"
thread = threading.Thread(
    target=uvicorn.run,
    args=(create_demo_app(),),
    kwargs={"host": "127.0.0.1", "port": 9001, "log_level": "warning"},
    daemon=True,
)
thread.start()
print(
    "Local demo: http://127.0.0.1:8080 (password from DASHBOARD_PASSWORD; default local-demo-2026!)"
)
uvicorn.run(create_app(cfg), host="127.0.0.1", port=8080, log_level="warning")
