"""合成备份到新目录后实际续跑，并拒绝覆盖、越界与坏内容。"""
import json
import zipfile

import pytest

from test_engineering_workbench import workbench, GOVERNED
from chanlun_trader.research_factory.engineering_workspace import save_engineering_workspace
from chanlun_trader.research_factory.engineering_backup import backup_engineering_workspace, restore_engineering_workspace
from chanlun_trader.research_factory.common import stable_hash

LIMIT = 20_000_000


def test_backup_restore_to_new_root_preserves_paper_and_continues(tmp_path):
    service = workbench(tmp_path / "original")
    config = save_engineering_workspace(tmp_path / "original/workbench.json", service)
    candidate = next(iter(service.sources))
    service.advance(GOVERNED, {"confirmed": True, "context_hash": service.inspect()["context_hash"], "candidate_id": candidate, "event_count": 9})
    before = service.inspect()
    archive = tmp_path / "backup.zip"
    manifest = backup_engineering_workspace(config, archive, max_bytes=LIMIT)
    assert manifest["real_execution_authorized"] is False
    restored = restore_engineering_workspace(archive, tmp_path / "restored", max_bytes=LIMIT)
    assert restored.inspect() == before
    result = restored.advance(GOVERNED, {"confirmed": True, "context_hash": restored.inspect()["context_hash"],
        "candidate_id": candidate, "event_count": 10})
    assert result["completed_events"] == 10
    assert service.inspect()["paper"][candidate]["completed_events"] == 9
    assert result["real_observation_days"] == 0
    with pytest.raises(ValueError, match="BACKUP_NEW_SEPARATE_DESTINATION_REQUIRED"):
        restore_engineering_workspace(archive, tmp_path / "restored", max_bytes=LIMIT)


def test_byte_limit_and_archive_tampering_fail_before_restore_write(tmp_path):
    service = workbench(tmp_path / "original")
    config = save_engineering_workspace(tmp_path / "original/workbench.json", service)
    archive = tmp_path / "backup.zip"
    with pytest.raises(ValueError, match="BACKUP_BYTE_LIMIT_EXCEEDED"):
        backup_engineering_workspace(config, archive, max_bytes=1)
    assert not archive.exists()
    manifest = backup_engineering_workspace(config, archive, max_bytes=LIMIT)
    with pytest.raises(ValueError, match="BACKUP_BYTE_LIMIT_EXCEEDED"):
        backup_engineering_workspace(config, tmp_path / "metadata-over-limit.zip", max_bytes=manifest["uncompressed_bytes"])
    with zipfile.ZipFile(archive) as bundle:
        payload = {name: bundle.read(name) for name in bundle.namelist()}
    payload["workbench.json"] += b" "
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(tampered, "x") as bundle:
        for name, content in payload.items():
            bundle.writestr(name, content)
    target = tmp_path / "not-created"
    with pytest.raises(ValueError, match="BACKUP_CONTENT_CHANGED"):
        restore_engineering_workspace(tampered, target, max_bytes=LIMIT)
    assert not target.exists()
    with zipfile.ZipFile(archive) as bundle:
        payload = {name: bundle.read(name) for name in bundle.namelist()}
    manifest = json.loads(payload["BACKUP_MANIFEST.json"])
    manifest["config"] = str(tmp_path / "outside/workbench.json")
    manifest["manifest_identity"] = stable_hash({key: value for key, value in manifest.items() if key != "manifest_identity"})
    payload["BACKUP_MANIFEST.json"] = json.dumps(manifest).encode()
    redirected = tmp_path / "redirected.zip"
    with zipfile.ZipFile(redirected, "x") as bundle:
        for name, content in payload.items():
            bundle.writestr(name, content)
    with pytest.raises(ValueError, match="BACKUP_MANIFEST_INVALID"):
        restore_engineering_workspace(redirected, target, max_bytes=LIMIT)
    assert not target.exists()


def test_zip_path_escape_rejected_without_writing(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "x") as bundle:
        bundle.writestr("../escaped.json", json.dumps({}))
    with pytest.raises(ValueError, match="BACKUP_UNSAFE_PATH"):
        restore_engineering_workspace(archive, tmp_path / "new", max_bytes=LIMIT)
    assert not (tmp_path / "new").exists()
