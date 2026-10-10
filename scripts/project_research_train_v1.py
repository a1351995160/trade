"""按固定部署和 Owner 授权，把已登记父资料投影为独立 TRAIN 目录。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))


def _pinned_json(path, digest):
    path = Path(path)
    if (not path.is_absolute() or path.resolve() != path or not path.is_file()
            or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)):
        raise ValueError('TRAIN_PROJECTION_DEPLOYMENT_REFERENCE_INVALID')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('TRAIN_PROJECTION_DEPLOYMENT_CHANGED')
    return json.loads(raw)


def build_train_projection(deployment_config, deployment_sha256, dataset_id):
    """仅使用已 pin 的维护者登记；CLI 不接收父路径、模块或 resolver。"""
    from chanlun_trader.research.guard import configured_access_authority
    from chanlun_trader.research_factory.research_dataset_projection_v1 import ResearchDatasetProjectionV1
    from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1

    config = _pinned_json(deployment_config, deployment_sha256)
    if (not isinstance(config, dict) or set(config) != {
            'schema_version', 'trusted_data_deployment', 'registered_parents'}
            or config['schema_version'] != 'TRUSTED_TRAIN_PROJECTION_DEPLOYMENT_V1'
            or not isinstance(config['registered_parents'], dict)):
        raise ValueError('TRAIN_PROJECTION_DEPLOYMENT_SCHEMA_INVALID')
    deployment = config['trusted_data_deployment']
    if (not isinstance(deployment, dict) or set(deployment) != {'path', 'sha256'}
            or not isinstance(deployment.get('path'), str) or not Path(deployment['path']).is_absolute()):
        raise ValueError('TRAIN_PROJECTION_AUTHORITY_REFERENCE_INVALID')
    authority = configured_access_authority(deployment['path'], expected_sha256=deployment['sha256'])
    parent = config['registered_parents'].get(dataset_id)
    if not isinstance(parent, dict) or set(parent) != {'root', 'manifest_path', 'manifest_sha256'}:
        raise ValueError('TRAIN_PROJECTION_PARENT_NOT_REGISTERED')
    root = Path(parent['root'])
    relative = parent['manifest_path']
    if (not root.is_absolute() or root.resolve() != root or not root.is_dir()
            or not isinstance(relative, str) or Path(relative).suffix.lower() != '.json'
            or Path(relative).anchor or PureWindowsPath(relative).anchor
            or '..' in Path(relative).parts or '..' in PureWindowsPath(relative).parts):
        raise ValueError('TRAIN_PROJECTION_PARENT_REGISTRATION_INVALID')
    # 这是维护者固定登记的元数据原件；行情文件只由投影服务获批后打开。
    metadata = UniverseDataProviderV1._path(root, relative)
    _pinned_json(metadata, parent['manifest_sha256'])
    provider = UniverseDataProviderV1({'registered_parent': root})
    provider.register(dataset_id, 'registered_parent', relative)
    if provider._datasets[dataset_id][2] != parent['manifest_sha256']:
        raise ValueError('TRAIN_PROJECTION_PARENT_METADATA_CHANGED')
    return ResearchDatasetProjectionV1(provider, authority)


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--deployment-config', required=True, help='维护者固定登记的投影配置绝对路径')
    cli.add_argument('--deployment-sha256', required=True, help='可信部署给出的配置 SHA256 pin')
    cli.add_argument('--dataset-id', required=True, help='配置中已登记的父 dataset_id')
    cli.add_argument('--authorization-ref', required=True, help='受保护 data authority 中已批准的引用')
    cli.add_argument('--output-root', required=True, help='新目录；父目录必须存在，目标不能已存在')
    cli.add_argument('--train-start', required=True, help='TRAIN 首日，YYYYMMDD 或 YYYY-MM-DD')
    cli.add_argument('--train-end', required=True, help='TRAIN 末日，YYYYMMDD 或 YYYY-MM-DD')
    cli.add_argument('--account-start', help='可选实际账户首日；用于披露可用交易日和缺口')
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    service = build_train_projection(args.deployment_config, args.deployment_sha256, args.dataset_id)
    result = service.project(args.dataset_id, output_root=args.output_root,
        train_start=args.train_start, train_end=args.train_end,
        authorization_ref=args.authorization_ref, account_start=args.account_start)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
