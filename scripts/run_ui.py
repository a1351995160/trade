"""启动 Web UI。用法：python scripts/run_ui.py [端口]"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import uvicorn

from chanlun_trader.webapp import app


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
