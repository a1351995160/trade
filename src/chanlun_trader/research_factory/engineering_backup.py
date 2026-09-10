"""显式合成工作区备份；恢复只写全新目录，不覆盖旧状态。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

from .common import canonical_json, stable_hash
from .engineering_workspace import load_engineering_workspace
from .mutation_boundary import ObjectiveMutationLock
from .source_dependencies import SOURCE_ROOT


def _new_destination(path, *, source=None):
    path = Path(path)
    if (not path.is_absolute() or path.resolve() != path or path.exists()
            or path.is_relative_to(SOURCE_ROOT) or SOURCE_ROOT.is_relative_to(path)
            or source is not None and (path.is_relative_to(source) or source.is_relative_to(path))):
        raise ValueError("BACKUP_NEW_SEPARATE_DESTINATION_REQUIRED")
    return path


def backup_engineering_workspace(config, archive, *, max_bytes: int):
    config = Path(config)
    service = load_engineering_workspace(config)
    archive = _new_destination(archive, source=config.parent)
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("BACKUP_BYTE_LIMIT_REQUIRED")
    with ObjectiveMutationLock.for_resource(service.output_root / "workbench"):
        # 只枚举配置声明的两棵树，不扫描其父目录中的其他项目或数据。
        paths = [config, *[path for root in (service.root, service.output_root)
            for path in root.rglob("*") if path.is_file()]]
        payload, total = {}, 0
        for path in sorted(paths):
            if path.resolve() != path:
                raise ValueError("BACKUP_LINKED_SOURCE")
            size = path.stat().st_size
            total += size
            if total > max_bytes:
                raise ValueError("BACKUP_BYTE_LIMIT_EXCEEDED")
            # Windows当前锁可锁住第一个字节；空文件按其实际零长度备份。
            content = path.read_bytes() if size else b""
            payload[path.relative_to(config.parent).as_posix()] = content
        load_engineering_workspace(config)
        manifest = {"schema_version": "engineering-backup-v1", "config": config.name,
            "files": {name: hashlib.sha256(content).hexdigest() for name, content in payload.items()},
            "uncompressed_bytes": total, "real_execution_authorized": False}
        manifest["manifest_identity"] = stable_hash(manifest)
        manifest_bytes = canonical_json(manifest).encode("utf-8")
        if total + len(manifest_bytes) > max_bytes:
            raise ValueError("BACKUP_BYTE_LIMIT_EXCEEDED")
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("BACKUP_MANIFEST.json", manifest_bytes)
            for name, content in payload.items():
                bundle.writestr(name, content)
    return manifest


def restore_engineering_workspace(archive, destination, *, max_bytes: int):
    destination = _new_destination(destination)
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("BACKUP_BYTE_LIMIT_REQUIRED")
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("BACKUP_DUPLICATE_PATH")
        for name in names:
            parts = PurePosixPath(name)
            if parts.is_absolute() or ".." in parts.parts or "\\" in name or ":" in name or not parts.parts:
                raise ValueError("BACKUP_UNSAFE_PATH")
        if sum(item.file_size for item in bundle.infolist()) > max_bytes:
            raise ValueError("BACKUP_BYTE_LIMIT_EXCEEDED")
        manifest = json.loads(bundle.read("BACKUP_MANIFEST.json"))
        if (manifest.get("schema_version") != "engineering-backup-v1" or manifest.get("real_execution_authorized") is not False
                or manifest.get("manifest_identity") != stable_hash({key: value for key, value in manifest.items() if key != "manifest_identity"})
                or set(names) != {"BACKUP_MANIFEST.json", *manifest["files"]}
                or manifest.get("config") not in manifest["files"]
                or PurePosixPath(manifest["config"]).parent != PurePosixPath(".")):
            raise ValueError("BACKUP_MANIFEST_INVALID")
        payload = {name: bundle.read(name) for name in manifest["files"]}
        if any(hashlib.sha256(content).hexdigest() != manifest["files"][name] for name, content in payload.items()):
            raise ValueError("BACKUP_CONTENT_CHANGED")
    destination.mkdir(parents=True)
    for name, content in payload.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
    # 仍只重建输入；不会因恢复备份而执行任何事件。
    return load_engineering_workspace(destination / manifest["config"])


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="明确配置的工程工作区备份/恢复；不覆盖旧目录")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--config", type=Path, required=True)
    backup.add_argument("--archive", type=Path, required=True)
    backup.add_argument("--max-bytes", type=int, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    restore.add_argument("--max-bytes", type=int, required=True)
    args = parser.parse_args()
    if args.command == "backup":
        result = backup_engineering_workspace(args.config, args.archive, max_bytes=args.max_bytes)
        print(canonical_json({"status": "BACKUP_WRITTEN", "manifest_identity": result["manifest_identity"], "file_count": len(result["files"])}))
    else:
        service = restore_engineering_workspace(args.archive, args.destination, max_bytes=args.max_bytes)
        print(canonical_json({"status": "RESTORED_WITHOUT_EXECUTION", "context_hash": service.inspect()["context_hash"], "real_execution_authorized": False}))
