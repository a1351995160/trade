"""启动 Web UI。用法：python scripts/run_ui.py [端口]"""
from __future__ import annotations

import sys
import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from chanlun_trader.webapp import create_app


def lifecycle_service(root, filename):
    from chanlun_trader.research_factory.lifecycle_service_v2 import LifecycleServiceV2
    if root is None:
        raise ValueError('LIFECYCLE_EXPLICIT_RESEARCH_ROOT_REQUIRED')
    root, filename = Path(root).absolute(), Path(filename).absolute()
    if (root.resolve() != root or filename.resolve() != filename or '..' in filename.parts
            or not filename.is_relative_to(root) or filename.suffix.lower() != '.json'
            or filename.stat().st_size > 20 * 1024 * 1024):
        raise ValueError('LIFECYCLE_CONFIG_OUTSIDE_WORKSPACE')
    config = json.loads(filename.read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict) or set(config) != {'bindings'}:
        raise ValueError('LIFECYCLE_DEPLOYMENT_CONFIG_FIELDS')
    from scripts.lifecycle_deployment_v2 import qualified_research_loader
    return LifecycleServiceV2(root, config['bindings'], research_loader=qualified_research_loader(root))


def main() -> None:
    parser = argparse.ArgumentParser(description="以只读模式启动 Web 控制台")
    parser.add_argument("port", nargs="?", type=int, default=8000)
    parser.add_argument("--research-root", type=Path, help="显式绑定只读研究目录")
    parser.add_argument('--lifecycle-config', type=Path, help='工作区内的显式生命周期部署配置 JSON')
    args = parser.parse_args()
    service = lifecycle_service(args.research_root, args.lifecycle_config) if args.lifecycle_config else None
    uvicorn.run(create_app(args.research_root, lifecycle_service=service), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
