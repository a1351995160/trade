"""启动 Web UI。用法：python scripts/run_ui.py [端口]"""
from __future__ import annotations

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import uvicorn

from chanlun_trader.webapp import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="以只读模式启动 Web 控制台")
    parser.add_argument("port", nargs="?", type=int, default=8000)
    parser.add_argument("--research-root", type=Path, help="显式绑定只读研究目录")
    args = parser.parse_args()
    uvicorn.run(create_app(args.research_root), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
